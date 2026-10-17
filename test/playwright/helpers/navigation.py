"""Navigate through the rendered header or its compact menu."""

import re

from playwright.sync_api import expect


def navigation_root(page):
    # The responsive header measures hidden links before choosing its layout.
    page.wait_for_load_state("networkidle")
    banner = page.get_by_role("banner")
    expect(banner).to_be_visible()
    menu = banner.get_by_role("button", name="Menu", exact=True)
    if menu.is_visible():
        menu.click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        return dialog
    return banner


def click_navigation(page, test_id: str, expected_path: str) -> None:
    link = navigation_root(page).get_by_test_id(test_id)
    expect(link).to_be_visible()
    link.click()
    expect(page).to_have_url(re.compile(re.escape(expected_path) + r"(?:[/?#].*)?$"))
