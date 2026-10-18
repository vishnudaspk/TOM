"""Manual smoke test for web.fetch — NOT collected by pytest (no test_ prefix on module).

Run with:  python tests/manual/web_fetch_smoke.py
Requires internet access. Never weakens security verification.
"""

import asyncio
import sys

from tom.tools.web import BuiltinFetchProvider, FetchProviderError, FetchRequest


async def _smoke() -> None:  # noqa: C901
    provider = BuiltinFetchProvider()
    cases = [
        # (label, url, expect_success, expect_error_fragment)
        (
            "Valid HTTPS public site",
            "https://example.com",
            True,
            None,
        ),
        (
            "Expired TLS certificate (badssl.com)",
            "https://expired.badssl.com/",
            False,
            None,  # any exception is expected
        ),
        (
            "Redirect chain to HTML (relative then absolute to example.com)",
            "https://httpbin.org/redirect-to?url=%2Fredirect-to%3Furl%3Dhttps%253A%252F%252Fexample.com",
            True,
            None,
        ),
    ]

    passed_cases = 0
    total_cases = len(cases)
    for label, url, expect_success, _ in cases:
        print(f"\n{'=' * 60}")
        print(f"  {label}")
        print(f"  URL: {url}")
        try:
            req = FetchRequest(url=url)
        except Exception as exc:
            print(f"  [SCHEMA ERROR] {exc}")
            continue

        try:
            resp = await provider.fetch(req)
            if expect_success:
                print(f"  [OK] status={resp.status_code} redirects_followed={resp.redirect_count}")
                print(f"       final_url={resp.final_url}")
                print(f"       content_type={resp.content_type} length={resp.content_length}")
                print(f"       untrusted={resp.untrusted}")
                passed_cases += 1
            else:
                print(f"  [UNEXPECTED SUCCESS] status={resp.status_code}  — expected an error")
        except FetchProviderError as exc:
            if not expect_success:
                print(f"  [EXPECTED ERROR (PASSED)] {type(exc).__name__}: {exc}")
                passed_cases += 1
            else:
                print(f"  [UNEXPECTED ERROR] {type(exc).__name__}: {exc}")
        except Exception as exc:
            if not expect_success:
                print(f"  [EXPECTED ERROR (PASSED)] {type(exc).__name__}: {exc}")
                passed_cases += 1
            else:
                print(f"  [UNEXPECTED ERROR] {type(exc).__name__}: {exc}")

    print(f"\n{'=' * 60}")
    print(f"Summary: {passed_cases}/{total_cases} cases matched expectations")
    if passed_cases == total_cases:
        print("SMOKE: PASS (all cases matched expectations)")
    else:
        print("SMOKE: FAILED — see errors above")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(_smoke())
