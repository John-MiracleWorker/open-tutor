"""Browser regression for Aurora's safe-area CSS (no app/data/model access).

Run with a Python environment containing Playwright:
  python scripts/check_safe_area.py [--browser /path/to/chromium]

Chromium headless has zero native safe-area insets, so substitute nonzero
values in the real stylesheet. This checks CSS geometry, not an OS keyboard.
"""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser")
    args = parser.parse_args()
    css = (Path(__file__).resolve().parents[1] / "web/src/style.css").read_text()
    cases = []
    with sync_playwright() as playwright:
        options = {"executable_path": args.browser} if args.browser else {"channel": "chrome"}
        browser = playwright.chromium.launch(headless=True, **options)
        try:
            for width, height in [(390, 844), (390, 420), (320, 420)]:
                context = browser.new_context(viewport={"width": width, "height": height})
                context.route("**/*", lambda route: route.abort())
                page = context.new_page()
                page.set_content('''<!doctype html><html><body>
                    <div class="app"><header class="navigation-dock">
                    <div class="brand">Open Tutor</div><nav aria-label="Workspace">
                    <button>Learn</button><button>Library</button>
                    <button>Research</button><button>Progress</button>
                    </nav><div class="top-actions">Settings</div></header>
                    <main class="workspace">Workspace</main></div></body></html>''')
                # Values are test-only substitutions, not production CSS overrides.
                page.add_style_tag(content=css.replace("env(safe-area-inset-top)", "47px")
                                   .replace("env(safe-area-inset-bottom)", "34px"))
                geometry = page.evaluate('''() => {
                    const app = document.querySelector('.app');
                    const nav = document.querySelector('nav');
                    const style = getComputedStyle(app);
                    return {top: parseFloat(style.paddingTop),
                        bottom: parseFloat(style.paddingBottom),
                        navHeight: nav.getBoundingClientRect().height,
                        navBottom: parseFloat(getComputedStyle(nav).paddingBottom),
                        buttonBottom: Math.max(...[...nav.children].map(e => e.getBoundingClientRect().bottom)),
                        workspaceTop: document.querySelector('.workspace').getBoundingClientRect().top};
                }''')
                cases.append({"viewport": [width, height], "geometry": geometry})
                assert geometry["top"] >= 47, cases[-1]
                assert geometry["navBottom"] >= 34, cases[-1]
                assert geometry["bottom"] >= geometry["navHeight"], cases[-1]
                assert geometry["buttonBottom"] <= height - 34, cases[-1]
                assert geometry["workspaceTop"] >= 47, cases[-1]
                context.close()
        finally:
            browser.close()
    print(json.dumps({"passed": True, "cases": cases}, indent=2))


if __name__ == "__main__":
    main()
