from __future__ import annotations

import unittest

import release.provision_ffmpeg as provision_ffmpeg


class FFmpegProvisioningTests(unittest.TestCase):
    @staticmethod
    def _source_manifest() -> dict[str, object]:
        return {
            "schema_version": 1,
            "provider": "Reviewed local build",
            "version": "9.0.1",
            "license": "GPL-3.0-or-later",
            "source_reference": "build-record-2026-08-30",
            "tools": {
                "ffmpeg": {"sha256": "1" * 64},
                "ffprobe": {"sha256": "2" * 64},
            },
        }

    def test_cache_filename_rejects_cross_platform_traversal_and_absolute_paths(
        self,
    ) -> None:
        self.assertEqual(
            provision_ffmpeg._validated_cache_filename(
                "ffmpeg-9.0.1.zip",
                suffix=".zip",
            ),
            "ffmpeg-9.0.1.zip",
        )
        for value in (
            "../ffmpeg.zip",
            "subdir/ffmpeg.zip",
            r"subdir\ffmpeg.zip",
            "/tmp/ffmpeg.zip",
            r"C:\temp\ffmpeg.zip",
            r"C:ffmpeg.zip",
            r"\\server\share\ffmpeg.zip",
        ):
            with self.subTest(value=value):
                with self.assertRaises(provision_ffmpeg.FFmpegProvisionError):
                    provision_ffmpeg._validated_cache_filename(value, suffix=".zip")

    def test_explicit_source_manifest_requires_exact_tool_set(self) -> None:
        manifest = self._source_manifest()
        validated = provision_ffmpeg._validate_explicit_source_manifest(
            manifest,
            expected_version="9.0.1",
        )
        self.assertEqual(set(validated["tools"]), {"ffmpeg", "ffprobe"})

        missing_tool = self._source_manifest()
        del missing_tool["tools"]["ffprobe"]  # type: ignore[index]
        with self.assertRaises(provision_ffmpeg.FFmpegProvisionError):
            provision_ffmpeg._validate_explicit_source_manifest(
                missing_tool,
                expected_version="9.0.1",
            )

        extra_tool = self._source_manifest()
        extra_tool["tools"]["ffplay"] = {"sha256": "3" * 64}  # type: ignore[index]
        with self.assertRaises(provision_ffmpeg.FFmpegProvisionError):
            provision_ffmpeg._validate_explicit_source_manifest(
                extra_tool,
                expected_version="9.0.1",
            )

    def test_tampered_explicit_source_hash_is_rejected(self) -> None:
        with self.assertRaises(provision_ffmpeg.FFmpegProvisionError):
            provision_ffmpeg._require_hash_match(
                "ffmpeg",
                "1" * 64,
                "2" * 64,
            )

    def test_explicit_source_provenance_uses_supplied_metadata(self) -> None:
        explicit_source = provision_ffmpeg._validate_explicit_source_manifest(
            self._source_manifest(),
            expected_version="9.0.1",
        )
        explicit_source["manifest_sha256"] = "a" * 64
        pinned_configuration = {
            "provider": "Pinned download provider",
            "version": "9.0.1",
            "license": "Pinned license",
            "tools": [
                {
                    "url": "https://example.invalid/ffmpeg.zip",
                    "archive_sha256": "b" * 64,
                    "binary_sha256": "c" * 64,
                }
            ],
        }

        lines = provision_ffmpeg._source_provenance_lines(
            [pinned_configuration],
            explicit_source,
        )

        self.assertIn("Provider: Reviewed local build", lines)
        self.assertIn("Source reference: build-record-2026-08-30", lines)
        self.assertFalse(any(line.startswith("Artifact:") for line in lines))
        self.assertNotIn("Provider: Pinned download provider", lines)


if __name__ == "__main__":
    unittest.main()
