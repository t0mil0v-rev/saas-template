import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({
        error: { code: "unauthenticated", message: "Войдите" },
        request_id: "e2e",
      }),
    }),
  );
});

test("landing states the template validation level", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Академически выверенный шаблон")).toBeVisible();
  await expect(page.getByRole("link", { name: "Посмотреть состав" })).toHaveAttribute(
    "href",
    "#capabilities",
  );
  const primaryAction = await page
    .getByRole("link", { name: "Создать рабочее пространство" })
    .boundingBox();
  const secondaryAction = await page
    .getByRole("link", { name: "Посмотреть состав" })
    .boundingBox();
  expect(primaryAction).not.toBeNull();
  expect(secondaryAction).not.toBeNull();
  expect(Math.abs((primaryAction?.y ?? 0) - (secondaryAction?.y ?? 0))).toBeLessThan(1);
  await page.getByRole("link", { name: "Посмотреть состав" }).click();
  await expect(page).toHaveURL(/#capabilities$/);
  await expect(page.getByRole("heading", { name: "Инженерный контур" })).toBeVisible();
  await page.waitForFunction(() =>
    document.getAnimations().every((animation) => animation.playState === "finished"),
  );
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
});

test("mobile hero keeps its actions inside the first viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  const primaryAction = page.getByRole("link", { name: "Создать рабочее пространство" });
  const actionBox = await primaryAction.boundingBox();
  expect(actionBox).not.toBeNull();
  expect((actionBox?.y ?? 0) + (actionBox?.height ?? 0)).toBeLessThanOrEqual(844);

  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(hasHorizontalOverflow).toBe(false);
});

test("a private route sends a signed-out user to login", async ({ page }) => {
  await page.goto("/app");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Вход" })).toBeVisible();
  await page.waitForFunction(() =>
    document.getAnimations().every((animation) => animation.playState === "finished"),
  );
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
});

test("registration submits the expected payload", async ({ page }) => {
  let requestBody: unknown;
  await page.route("**/api/auth/register", async (route) => {
    requestBody = route.request().postDataJSON();
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Проверьте почту" }),
    });
  });

  await page.goto("/register");
  await page.getByLabel("Имя").fill("Ada Lovelace");
  await page.getByLabel("E-mail").fill("ada@example.com");
  await page.getByLabel("Пароль").fill("correct horse battery staple");
  await page.getByLabel("Название организации (необязательно)").fill("Analytical Engines");
  await page.getByRole("button", { name: "Зарегистрироваться" }).click();

  await expect(page.getByText("Проверьте почту")).toBeVisible();
  expect(requestBody).toEqual({
    email: "ada@example.com",
    password: "correct horse battery staple",
    full_name: "Ada Lovelace",
    org_name: "Analytical Engines",
  });
});
