"""Page-aware composition and guarded batches using the canonical project APIs."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
import re

from project_model import PAGE_TYPES, ProjectModel


def page_label(project, page_id):
    page = project.get_page(page_id)
    kind = PAGE_TYPES[page["type"]].label
    if page["name"] and page["name"] != kind:
        return page["name"]
    peers = [item for item in project.pages if item["type"] == page["type"]]
    return kind if len(peers) == 1 else f"{kind} {next(i for i, item in enumerate(peers, 1) if item['id'] == page_id)}"


def compose_page(project, page_id, resolved, guidance_phrases, *, allow_empty_subject=False):
    # Import at composition time: the panel also uses this UI-independent service.
    from PromptWriterPanel import PAGE_SPECS, assemble_prompt_for_image
    context = project.prompt_context(page_id)
    geometry = project.prompt_geometry(page_id)
    choices = {**context["shared"], **context["prompt"]["overrides"]}
    checks = choices.get("checks") or {}
    guidance = [phrase for key, phrase in guidance_phrases.items() if checks.get(key)]
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
        template.baseline = ('Create a closing image that conveys a sense of finality. '
                             'Include the exact words “The End” as part of the artwork.')
    template.baseline = f"Create exactly one image for this {PAGE_TYPES[context['type']].label} page. {template.baseline}"
    data = deepcopy(resolved)
    # Older seed libraries describe an entire multi-page sequence. Such a role
    # must not turn a single-page request into a request for a set of images.
    role = str(data.get("role") or "an expert visual designer")
    if re.search(r"\b(?:pages?|images|sequences?|sets|collections?|continuity)\b", role, re.I):
        role = "an expert visual designer creating one self-contained page image"
    data["role"] = role
    if "order" not in data and "subject_lead_in" in data:
        data["order"] = data["subject_lead_in"]
    # Seeded lead-ins must not force portrait framing when no framing is chosen.
    order = data.get('order', '')
    def neutral_order(value):
        return re.sub(r'\ba portrait(?: image)?\b', 'an image', str(value or ''), flags=re.I)
    data['order'] = [neutral_order(value) for value in order] if isinstance(order, (list, tuple)) else neutral_order(order)
    # Preserve useful source-library quality/framing prose, replacing only old
    # output-size assumptions in a fresh build. Historical strings are untouched.
    format_text = str(data.get("format") or "")
    format_text = re.sub(r"\b\d+\s*[×xX]\s*\d+\s*pixels?\b", "the requested pixel dimensions", format_text)
    format_text = re.sub(r"\b(portrait|landscape|square)\b", geometry["orientation"], format_text, flags=re.I)
    data["format"] = (
        f"Output a {geometry['orientation']} image at exactly {geometry['width']}×{geometry['height']} pixels "
        f"({geometry['aspect_ratio']} aspect ratio). {format_text}"
    )
    prompt, _, _ = assemble_prompt_for_image(
        choices.get("subject") or "", data, template, type_choice=choices.get("type"),
        color_choice=choices.get("color"), guidance=guidance,
        global_extra=choices.get("global"), image_extra=context["prompt"]["directions"],
    )
    prompt += ('\nMaintain a cohesive artistic identity across all pages, '
               'while allowing each page its own creative direction.')
    return prompt, {"shared": deepcopy(resolved), "choices": choices, "geometry": geometry,
                    "generated_text": prompt}


def resolve_page_prompt(project, page_id, factory, guidance_phrases):
    """Resolve and persist current text, preserving authored and historical wording."""
    prompt = project.get_prompt(page_id)
    previous = prompt['resolved_components'].get('generated_text')
    if prompt['text'] and prompt['text'].strip() and previous is None:
        # No trustworthy baseline exists for older/manual text. Keep it exact.
        return prompt['text']
    resolved = project.resolve_prompt_components(factory)
    generated, components = compose_page(project, page_id, resolved, guidance_phrases)
    text = generated if prompt['text'] is None or previous is None else merge_live_prompt(previous, prompt['text'], generated)
    project.update_prompt(page_id, {'text': text, 'resolved_components': components, 'stale': False})
    return text


def merge_live_prompt(previous, authored, generated):
    """Refresh unchanged generated lines; overlapping author edits win."""
    if authored == previous:
        return generated
    if previous == generated or not previous:
        return authored
    previous_lines = previous.splitlines(keepends=True)
    authored_lines = authored.splitlines(keepends=True)
    generated_lines = generated.splitlines(keepends=True)
    matches = SequenceMatcher(None, previous_lines, authored_lines, autojunk=False).get_matching_blocks()
    edits = []
    for tag, start, end, new_start, new_end in SequenceMatcher(None, previous_lines, generated_lines, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        for match in matches:
            if match.size and match.a <= start <= end <= match.a + match.size:
                offset = match.b - match.a
                edits.append((start + offset, end + offset, generated_lines[new_start:new_end]))
                break
    for start, end, text in reversed(edits):
        authored_lines[start:end] = text
    return ''.join(authored_lines)


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
