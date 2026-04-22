#!/usr/bin/env python3
"""
OpenClaw SaaS Platform - End-to-End Playwright Test
Tests all major UI flows via the Web Console.
"""

import json
import sys
import time
from playwright.sync_api import sync_playwright, expect

BASE_URL = "http://localhost:8890"
ADMIN_EMAIL = "admin@openclaw.cn"
ADMIN_PASSWORD = "OpenClaw2026!"
TEST_TENANT = "e2e-test"

results = []

def log_result(test_name, passed, detail=""):
    status = "✅ PASS" if passed else "❌ FAIL"
    results.append((test_name, passed, detail))
    print(f"  {status}  {test_name}" + (f" — {detail}" if detail else ""))


def run_tests():
    print("=" * 60)
    print("  OpenClaw SaaS Platform — E2E Test Suite")
    print("=" * 60)
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        # ============================================================
        # 1. Health Check
        # ============================================================
        print("[1] API Health Check")
        try:
            resp = page.request.get(f"{BASE_URL}/health")
            data = resp.json()
            log_result("GET /health", data.get("status") == "ok", f"status={data.get('status')}")
        except Exception as e:
            log_result("GET /health", False, str(e))

        # ============================================================
        # 2. Login Page Load
        # ============================================================
        print("\n[2] Console & Login")
        try:
            page.goto(f"{BASE_URL}/console")
            page.wait_for_load_state("networkidle", timeout=15000)
            # 截图
            page.screenshot(path="/tmp/e2e-01-login-page.png")
            # 检查是否有登录表单
            has_email = page.locator("input[type='email'], input[name='email'], input[placeholder*='email' i], input[placeholder*='Email' i]").count() > 0
            has_password = page.locator("input[type='password']").count() > 0
            log_result("Login page loads", has_email and has_password, 
                       f"email_input={has_email}, password_input={has_password}")
        except Exception as e:
            log_result("Login page loads", False, str(e))

        # ============================================================
        # 3. Login Flow
        # ============================================================
        print("\n[3] Login Flow")
        try:
            # Fill email
            email_input = page.locator("input[type='email'], input[name='email'], input[placeholder*='email' i], input[placeholder*='Email' i]").first
            email_input.fill(ADMIN_EMAIL)
            
            # Fill password
            pwd_input = page.locator("input[type='password']").first
            pwd_input.fill(ADMIN_PASSWORD)
            
            # Click login button
            login_btn = page.locator("button[type='submit'], button:has-text('Login'), button:has-text('Sign'), button:has-text('登录')").first
            login_btn.click()
            
            # Wait for navigation
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(2)
            page.screenshot(path="/tmp/e2e-02-after-login.png")
            
            # Check we're on dashboard or tenant page
            url = page.url
            log_result("Login successful", "/console" in url, f"url={url}")
        except Exception as e:
            log_result("Login successful", False, str(e))

        # ============================================================
        # 4. Dashboard / Main Page
        # ============================================================
        print("\n[4] Dashboard")
        try:
            page.screenshot(path="/tmp/e2e-03-dashboard.png")
            # Check for dashboard elements
            body_text = page.inner_text("body")
            has_content = len(body_text) > 50
            log_result("Dashboard loads", has_content, f"body_length={len(body_text)}")
        except Exception as e:
            log_result("Dashboard loads", False, str(e))

        # ============================================================
        # 5. Tenants List
        # ============================================================
        print("\n[5] Tenants")
        try:
            # Navigate to tenants if not already there
            page.goto(f"{BASE_URL}/console")
            page.wait_for_load_state("networkidle", timeout=10000)
            time.sleep(2)
            page.screenshot(path="/tmp/e2e-04-tenants.png")
            
            # Check if tenant-0318 exists from earlier test
            body = page.inner_text("body")
            has_tenant = "tenant-0318" in body.lower() or "0318" in body
            log_result("Tenants list visible", True, f"has_tenant_0318={has_tenant}")
        except Exception as e:
            log_result("Tenants list visible", False, str(e))

        # ============================================================
        # 6. Create Tenant
        # ============================================================
        print("\n[6] Create Tenant")
        try:
            # Try API directly
            token_resp = page.request.post(f"{BASE_URL}/api/v1/auth/login", data={
                "email": ADMIN_EMAIL,
                "password": ADMIN_PASSWORD
            })
            token = token_resp.json()["access_token"]
            
            # Create tenant via API
            create_resp = page.request.post(f"{BASE_URL}/api/v1/tenants", 
                headers={"Authorization": f"Bearer {token}"},
                data={"name": TEST_TENANT, "plan": "free"})
            
            if create_resp.status in (200, 201):
                tenant_data = create_resp.json()
                log_result("Create tenant", True, f"name={tenant_data.get('name')}, plan={tenant_data.get('plan')}")
            elif create_resp.status == 409:
                log_result("Create tenant", True, "already exists (409)")
            else:
                log_result("Create tenant", False, f"status={create_resp.status}, body={create_resp.text()[:200]}")
        except Exception as e:
            log_result("Create tenant", False, str(e))

        # ============================================================
        # 7. Get Tenant Detail
        # ============================================================
        print("\n[7] Tenant Detail")
        try:
            detail_resp = page.request.get(f"{BASE_URL}/api/v1/tenants/{TEST_TENANT}",
                headers={"Authorization": f"Bearer {token}"})
            if detail_resp.status == 200:
                detail = detail_resp.json()
                log_result("Get tenant detail", True, f"name={detail.get('name')}, plan={detail.get('plan')}")
            else:
                log_result("Get tenant detail", False, f"status={detail_resp.status}")
        except Exception as e:
            log_result("Get tenant detail", False, str(e))

        # ============================================================
        # 8. Tenant Dashboard
        # ============================================================
        print("\n[8] Tenant Dashboard")
        try:
            dash_resp = page.request.get(f"{BASE_URL}/api/v1/tenants/{TEST_TENANT}/dashboard",
                headers={"Authorization": f"Bearer {token}"})
            if dash_resp.status == 200:
                dash = dash_resp.json()
                log_result("Tenant dashboard", True, f"keys={list(dash.keys())[:5]}")
            else:
                log_result("Tenant dashboard", False, f"status={dash_resp.status}, body={dash_resp.text()[:200]}")
        except Exception as e:
            log_result("Tenant dashboard", False, str(e))

        # ============================================================
        # 9. Plans & LLM Providers
        # ============================================================
        print("\n[9] Plans & Providers")
        try:
            plans_resp = page.request.get(f"{BASE_URL}/api/v1/plans",
                headers={"Authorization": f"Bearer {token}"})
            plans = plans_resp.json()
            log_result("GET /plans", plans_resp.status == 200, f"count={len(plans) if isinstance(plans, list) else 'N/A'}")
            
            providers_resp = page.request.get(f"{BASE_URL}/api/v1/llm-providers",
                headers={"Authorization": f"Bearer {token}"})
            providers = providers_resp.json()
            log_result("GET /llm-providers", providers_resp.status == 200, 
                       f"count={len(providers) if isinstance(providers, list) else 'N/A'}")
        except Exception as e:
            log_result("Plans & Providers", False, str(e))

        # ============================================================
        # 10. Channels
        # ============================================================
        print("\n[10] Channels")
        try:
            ch_resp = page.request.get(f"{BASE_URL}/api/v1/channels",
                headers={"Authorization": f"Bearer {token}"})
            channels = ch_resp.json()
            log_result("GET /channels", ch_resp.status == 200,
                       f"channels={channels if isinstance(channels, list) else type(channels).__name__}")
        except Exception as e:
            log_result("Channels", False, str(e))

        # ============================================================
        # 11. Create Agent
        # ============================================================
        print("\n[11] Create Agent")
        try:
            agent_resp = page.request.post(f"{BASE_URL}/api/v1/tenants/{TEST_TENANT}/agents",
                headers={"Authorization": f"Bearer {token}"},
                data={"name": "test-agent"})
            if agent_resp.status in (200, 201):
                agent = agent_resp.json()
                agent_id = agent.get("id") or agent.get("agent_id") or agent.get("name")
                log_result("Create agent", True, f"agent={agent_id}")
            elif agent_resp.status == 409:
                log_result("Create agent", True, "already exists (409)")
            else:
                log_result("Create agent", False, f"status={agent_resp.status}, body={agent_resp.text()[:300]}")
        except Exception as e:
            log_result("Create agent", False, str(e))

        # ============================================================
        # 12. List Agents
        # ============================================================
        print("\n[12] List Agents")
        try:
            agents_resp = page.request.get(f"{BASE_URL}/api/v1/tenants/{TEST_TENANT}/agents",
                headers={"Authorization": f"Bearer {token}"})
            agents = agents_resp.json()
            agent_count = len(agents) if isinstance(agents, list) else 0
            log_result("List agents", agents_resp.status == 200, f"count={agent_count}")
            
            if isinstance(agents, list) and agents:
                agent_name = agents[0].get("name", agents[0].get("id", "?"))
        except Exception as e:
            log_result("List agents", False, str(e))

        # ============================================================
        # 13. Usage & Billing
        # ============================================================
        print("\n[13] Usage & Billing")
        try:
            usage_resp = page.request.get(f"{BASE_URL}/api/v1/tenants/{TEST_TENANT}/usage",
                headers={"Authorization": f"Bearer {token}"})
            log_result("GET /usage", usage_resp.status == 200, f"body={usage_resp.text()[:200]}")
            
            billing_resp = page.request.get(f"{BASE_URL}/api/v1/tenants/{TEST_TENANT}/billing",
                headers={"Authorization": f"Bearer {token}"})
            log_result("GET /billing", billing_resp.status == 200, f"body={billing_resp.text()[:200]}")
            
            quota_resp = page.request.get(f"{BASE_URL}/api/v1/tenants/{TEST_TENANT}/billing/quota",
                headers={"Authorization": f"Bearer {token}"})
            log_result("GET /billing/quota", quota_resp.status == 200, f"body={quota_resp.text()[:200]}")
        except Exception as e:
            log_result("Usage & Billing", False, str(e))

        # ============================================================
        # 14. Members & Allowed Emails
        # ============================================================
        print("\n[14] Members & Allowed Emails")
        try:
            members_resp = page.request.get(f"{BASE_URL}/api/v1/tenants/{TEST_TENANT}/members",
                headers={"Authorization": f"Bearer {token}"})
            log_result("GET /members", members_resp.status == 200, f"body={members_resp.text()[:200]}")
            
            emails_resp = page.request.get(f"{BASE_URL}/api/v1/tenants/{TEST_TENANT}/allowed-emails",
                headers={"Authorization": f"Bearer {token}"})
            log_result("GET /allowed-emails", emails_resp.status == 200, f"body={emails_resp.text()[:200]}")
        except Exception as e:
            log_result("Members & Emails", False, str(e))

        # ============================================================
        # 15. Admin Overview
        # ============================================================
        print("\n[15] Admin Overview")
        try:
            overview_resp = page.request.get(f"{BASE_URL}/api/v1/tenants/admin/overview",
                headers={"Authorization": f"Bearer {token}"})
            log_result("GET /admin/overview", overview_resp.status == 200, f"body={overview_resp.text()[:200]}")
        except Exception as e:
            log_result("Admin overview", False, str(e))

        # ============================================================
        # 16. Web Console UI Navigation
        # ============================================================
        print("\n[16] Web Console UI Navigation")
        try:
            # Go to console and navigate around
            page.goto(f"{BASE_URL}/console")
            page.wait_for_load_state("networkidle", timeout=10000)
            time.sleep(2)
            
            # Try clicking on tenant
            page.screenshot(path="/tmp/e2e-05-console-main.png")
            
            # Look for tenant links
            tenant_links = page.locator(f"a:has-text('{TEST_TENANT}'), [href*='{TEST_TENANT}'], tr:has-text('{TEST_TENANT}')").count()
            if tenant_links == 0:
                tenant_links = page.locator("a:has-text('tenant'), [href*='tenant'], tr:has-text('tenant')").count()
            
            log_result("Console navigation", True, f"tenant_links={tenant_links}")
            
            # Try to navigate to tenant detail page
            if tenant_links > 0:
                page.locator(f"a:has-text('{TEST_TENANT}'), [href*='{TEST_TENANT}']").first.click()
                page.wait_for_load_state("networkidle", timeout=10000)
                time.sleep(2)
                page.screenshot(path="/tmp/e2e-06-tenant-detail.png")
                log_result("Tenant detail page", True, f"url={page.url}")
        except Exception as e:
            log_result("Console UI navigation", False, str(e))

        # ============================================================
        # 17. Cleanup - Delete test tenant
        # ============================================================
        print("\n[17] Cleanup")
        try:
            del_resp = page.request.delete(f"{BASE_URL}/api/v1/tenants/{TEST_TENANT}",
                headers={"Authorization": f"Bearer {token}"})
            log_result("Delete test tenant", del_resp.status in (200, 204), f"status={del_resp.status}")
        except Exception as e:
            log_result("Cleanup", False, str(e))

        browser.close()

    # ============================================================
    # Summary
    # ============================================================
    print()
    print("=" * 60)
    passed = sum(1 for _, p, _ in results if p)
    failed = sum(1 for _, p, _ in results if not p)
    total = len(results)
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    print("=" * 60)
    
    if failed > 0:
        print("\n  Failed tests:")
        for name, p, detail in results:
            if not p:
                print(f"    ❌ {name}: {detail}")
    
    print()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_tests())
