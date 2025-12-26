"""
Manual smoke test for the public user registration flow.

Run (inside docker):
  docker compose exec api uv run python test/register_smoke.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import string
from dataclasses import dataclass

import httpx
from dotenv import load_dotenv


def _random_suffix(length: int = 8) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def _random_cn_phone_number() -> str:
    prefix = random.choice(["130", "131", "132", "133", "135", "136", "137", "138", "139", "150", "151", "152", "155"])
    return prefix + "".join(random.choice(string.digits) for _ in range(8))


def _assert_status(response: httpx.Response, expected: int) -> None:
    if response.status_code != expected:
        raise AssertionError(f"Expected HTTP {expected}, got {response.status_code}: {response.text}")


@dataclass(frozen=True)
class AdminAuth:
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


async def _maybe_get_admin_auth(client: httpx.AsyncClient) -> AdminAuth | None:
    admin_login = os.getenv("TEST_USERNAME")
    admin_password = os.getenv("TEST_PASSWORD")
    if not admin_login or not admin_password:
        return None

    response = await client.post("/api/auth/token", data={"username": admin_login, "password": admin_password})
    _assert_status(response, 200)
    token = response.json().get("access_token")
    if not token:
        raise AssertionError("Admin login succeeded but no access_token returned.")
    return AdminAuth(token=token)


async def main() -> int:
    load_dotenv(".env", override=False)
    load_dotenv("test/.env.test", override=False)

    parser = argparse.ArgumentParser(description="Smoke test the /api/auth/register flow against a running API.")
    parser.add_argument("--base-url", default=os.getenv("TEST_BASE_URL", "http://localhost:5050").rstrip("/"))
    parser.add_argument(
        "--no-cleanup", action="store_true", help="Do not delete the created user (requires admin creds)."
    )
    args = parser.parse_args()

    async with httpx.AsyncClient(base_url=args.base_url, timeout=30.0, follow_redirects=True) as client:
        admin_auth = await _maybe_get_admin_auth(client)

        username = f"smoke_{_random_suffix()}"
        password = f"Pw!{_random_suffix(10)}"
        phone_number = _random_cn_phone_number()

        print("[1] register: success")
        register_response = await client.post(
            "/api/auth/register",
            json={"username": username, "password": password, "phone_number": phone_number},
        )
        _assert_status(register_response, 200)
        registered_user = register_response.json()
        assert registered_user["username"] == username
        assert registered_user["phone_number"] == phone_number
        assert registered_user["role"] == "user"
        assert registered_user["user_id"]
        assert registered_user["id"]

        print("[2] login: by user_id")
        login_user_id_response = await client.post(
            "/api/auth/token",
            data={"username": registered_user["user_id"], "password": password},
        )
        _assert_status(login_user_id_response, 200)
        assert login_user_id_response.json().get("access_token")

        print("[3] login: by phone_number")
        login_phone_response = await client.post(
            "/api/auth/token",
            data={"username": phone_number, "password": password},
        )
        _assert_status(login_phone_response, 200)
        assert login_phone_response.json().get("access_token")

        print("[4] register: duplicate username -> 400")
        duplicate_username_response = await client.post(
            "/api/auth/register",
            json={"username": username, "password": "irrelevant", "phone_number": _random_cn_phone_number()},
        )
        _assert_status(duplicate_username_response, 400)

        print("[5] register: duplicate phone -> 400")
        duplicate_phone_response = await client.post(
            "/api/auth/register",
            json={"username": f"smoke_{_random_suffix()}", "password": "irrelevant", "phone_number": phone_number},
        )
        _assert_status(duplicate_phone_response, 400)

        print("[6] register: invalid username -> 400")
        invalid_username_response = await client.post(
            "/api/auth/register",
            json={"username": "a", "password": "irrelevant"},
        )
        _assert_status(invalid_username_response, 400)

        print("[7] register: invalid phone -> 400")
        invalid_phone_response = await client.post(
            "/api/auth/register",
            json={"username": f"smoke_{_random_suffix()}", "password": "irrelevant", "phone_number": "12345"},
        )
        _assert_status(invalid_phone_response, 400)

        if not args.no_cleanup and admin_auth:
            print("[8] cleanup: delete created user")
            delete_response = await client.delete(
                f"/api/auth/users/{registered_user['id']}", headers=admin_auth.headers
            )
            _assert_status(delete_response, 200)

        print("OK: registration flow looks good.")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
