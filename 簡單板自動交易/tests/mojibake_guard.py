from __future__ import annotations

BAD_MOJIBAKE_TOKENS = (
    "?\uf5fb",
    "?\uea54",
    "?\x80",
    "\u5697",
    "\u929d",
    "\u8751",
    "\u648c",
    "\u978e",
    "\u761d",
    "\u7508",
    "\u96ff",
    "\u981d",
    "\u95ac",
)

LEGACY_ENGLISH_STATUS_TOKENS = (
    "task log found",
    "scheduled task query ok via task xml",
    "Rendered workflow status with",
    "Selection and sizing artifacts exist",
    "not required for direct A-preselect provider",
    "provider final_list artifact ready for direct A-preselect provider",
)


def assert_text_has_no_known_mojibake(testcase, text: str) -> None:
    for token in BAD_MOJIBAKE_TOKENS:
        testcase.assertNotIn(token, text)


def assert_text_has_no_legacy_english_status_tokens(testcase, text: str) -> None:
    for token in LEGACY_ENGLISH_STATUS_TOKENS:
        testcase.assertNotIn(token, text)
