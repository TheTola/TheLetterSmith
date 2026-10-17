"""Page-aware composition and guarded batches using the canonical project APIs."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re

from project_model import PAGE_TYPES, ProjectModel


def page_label(project, page_id):
    page = project.get_page(page_id)
    position = project.prompt_context(page_id)["position"]
    name = f"{page['name']} · " if page["name"] else ""
    return f"Page {position} of {len(project.pages)} — {name}{PAGE_TYPES[page['type']].label}"


def compose_page(project, page_id, resolved, guidance_phrases):
    # Import at composition time: the panel also uses this UI-independent service.
    from PromptWriterPanel import (
        PAGE_SPECS, HIDDEN_STYLE_DEFAULT, HIDDEN_FRAMING_DEFAULT,
        assemble_prompt_for_image,
    )
    context = project.prompt_context(page_id)
    geometry = project.prompt_geometry(page_id)
    choices = {**context["shared"], **context["prompt"]["overrides"]}
    if not str(choices.get("subject", "")).strip():
        raise ValueError("Choose a project subject or a subject override for this page.")
    checks = choices.get("checks", {})
    guidance = [phrase for key, phrase in guidance_phrases.items() if checks.get(key)]
    if not any(checks.get(key) for key in ("real", "paint", "minimal")):
        guidance.append(HIDDEN_STYLE_DEFAULT)
    if not any(checks.get(key) for key in ("close_up_focus", "full_body_view", "wide_scene")):
        guidance.append(HIDDEN_FRAMING_DEFAULT)
    if context["type"] == "end_page" and checks.get("forbid_text"):
        guidance.remove(guidance_phrases["forbid_text"])
    template = next(spec for spec in PAGE_SPECS if spec.key == context["template_id"])
    from dataclasses import replace
    template = replace(template)
    if context["type"] == "illustration":
        template.baseline = (
            "Create a complete artwork-only illustration of the requested subjects, characters, scenery and action. "
            "Let the selected style and page direction guide its composition; no message area is required."
        )
    elif context["type"] == "letter":
        area = geometry["text_area"]
        if area.get("mode") == "responsive_center":
            region = "a centered, responsive text panel between the viewer's top and bottom controls; its height depends on the text"
        else:
            scale = 100 if area["units"] == "normalized" else 1
            unit = "% of the image" if scale == 100 else "pixels"
            region = (f"a rectangle measured from the top-left: x={area['x'] * scale:g}, "
                      f"y={area['y'] * scale:g}, width={area['width'] * scale:g}, "
                      f"height={area['height'] * scale:g} ({unit})")
        template.baseline = (
            f"Create an illustrated background with a readable area for separately rendered message text in {region}. "
            "Use local contrast and composition to support readability while preserving artistic detail elsewhere. "
            "Follow the selected visual style; do not render the letter body into the artwork."
        )
    elif context["type"] == "end_page":
        template.baseline += ' Include the exact words “The End” as part of the artwork, with visual continuity to the opening illustration.'
    data = deepcopy(resolved)
    data["role"] = re.sub(
        r"\b(?:four|4)(?=-page|\s+(?:(?:connected|related|distinct but clearly related)\s+)?(?:pages?|images?)\b)",
        str(context["page_count"]), str(data.get("role", "Artist")), flags=re.I,
    )
    if "order" not in data and "subject_lead_in" in data:
        data["order"] = data["subject_lead_in"]
    # Preserve useful source-library quality/framing prose, replacing only old
    # output-size assumptions in a fresh build. Historical strings are untouched.
    format_text = str(data.get("format", ""))
    format_text = re.sub(r"\b\d+\s*[×xX]\s*\d+\s*pixels?\b", "the requested pixel dimensions", format_text)
    format_text = re.sub(r"\b(portrait|landscape|square)\b", geometry["orientation"], format_text, flags=re.I)
    data["format"] = (
        f"Output a {geometry['orientation']} image at exactly {geometry['width']}×{geometry['height']} pixels "
        f"({geometry['aspect_ratio']} aspect ratio). {format_text}"
    )
    prompt, _, _ = assemble_prompt_for_image(
        choices["subject"], data, template, type_choice=choices.get("type"),
        color_choice=choices.get("color"), guidance=guidance,
        global_extra=choices.get("global"), image_extra=context["prompt"]["directions"],
    )
    return prompt, {"shared": deepcopy(resolved), "choices": choices, "geometry": geometry}


@dataclass
class PromptItem:
    page_id: str
    label: str
    target: object
    replaces: bool
    status: str = "pending"
    detail: str = ""


class PromptBatch:
    """Transient work only. Each successful output is committed through the model."""
    def __init__(self, session, page_ids, factory, guidance_phrases):
        self.session = session
        self.cancelled = False
        self.guidance = dict(guidance_phrases)
        self.snapshot, self.resolved, targets = session.capture_prompt_batch(page_ids, factory)
        self.order = tuple(page["id"] for page in self.snapshot.pages)
        self.items = [PromptItem(target.page_id, page_label(self.snapshot, target.page_id), target,
                                self.snapshot.get_prompt(target.page_id)["text"] is not None)
                      for target in targets]

    def cancel(self):
        self.cancelled = True

    def run_item(self, item):
        if item.status != "pending":
            return item
        if self.cancelled:
            item.status = "skipped"
            item.detail = "Cancelled; previous prompt retained."
            return item
        try:
            text, resolved = compose_page(self.snapshot, item.page_id, self.resolved, self.guidance)
            applied = []
            def apply(model, page_id):
                if tuple(page["id"] for page in model.pages) != self.order:
                    return
                model.replace_prompt(page_id, {"text": text, "resolved_components": resolved, "stale": False})
                applied.append(True)
            self.session.apply_result(item.target, apply)
            item.status = "success" if applied else "conflict"
            item.detail = "Built." if applied else "The original project, page or inputs changed; previous prompt retained."
        except Exception as error:
            item.status, item.detail = "failed", str(error)
        return item
