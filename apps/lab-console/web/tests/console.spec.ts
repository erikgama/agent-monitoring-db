import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Laboratório de agentes" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Entrar no laboratório" }),
  ).toHaveCount(0);
});

test("canvas, three alerts, artifact isolation, event trace and no browser errors", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const name of [
    "Health Check",
    "Audit Security",
    "MCP Central",
    "Notification",
    "DBA",
    "Refactor",
  ])
    await expect(
      page.getByRole("article", { name: `Agente ${name}`, exact: true }),
    ).toBeVisible();
  const mcpCard = page.getByRole("article", {
    name: "Agente MCP Central",
    exact: true,
  });
  await expect(mcpCard).toContainText("Health/Audit → Notification + DBA");
  await expect(mcpCard).toContainText("Health → Refactor");
  await expect(mcpCard).toContainText("Refactor → DBA + Notification");
  await expect(mcpCard).toContainText("3 tools MCP");
  await expect(mcpCard).toContainText(
    "Publicadores: Health 2 · Audit 1 · Refactor 1",
  );
  const refactorCard = page.getByRole("article", {
    name: "Agente Refactor",
    exact: true,
  });
  for (const resource of ["Skill", "Rules", "Scripts"])
    await expect(
      refactorCard.getByRole("button", { name: resource, exact: true }),
    ).toBeVisible();
  await expect(refactorCard).toContainText(
    "request.json → Advisor → proposed.sql",
  );
  await expect(refactorCard).toContainText(
    "Validação sakila_dev → result.json → MCP",
  );
  const dbaCard = page.getByRole("article", {
    name: "Agente DBA",
    exact: true,
  });
  for (const resource of [
    "Skill",
    "Rule Health Check",
    "Rule Audit",
    "Scripts",
  ])
    await expect(
      dbaCard.getByRole("button", { name: resource, exact: true }),
    ).toBeVisible();
  await expect(
    dbaCard.getByRole("button", { name: "Rules", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Tentar DROP", exact: false }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Executar 3 alertas", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Expandir atividade ao vivo" })
    .click();
  await page.getByRole("button", { name: "Começar demonstração" }).click();
  await expect(
    page.getByRole("button", { name: "Iniciar processo" }),
  ).toBeDisabled();
  await page.getByLabel("Digite sakila para confirmar").fill("sakila");
  await page.getByRole("button", { name: "Iniciar processo" }).click();
  await expect(
    page.locator(".event-item").filter({ hasText: "E-mail enviado" }),
  ).toHaveCount(3, { timeout: 30000 });
  await expect(
    page.locator(".event-item").filter({ hasText: "Evidência registrada" }),
  ).toHaveCount(3);
  await page
    .getByRole("button", { name: "Biblioteca de evidências", exact: true })
    .click();
  await expect(page.locator(".artifact-item")).toHaveCount(3);
  const frame = page.locator("iframe");
  await expect(frame).toHaveAttribute("sandbox", "");
  await expect(
    page
      .frameLocator("iframe")
      .getByText("Schema sakila · Severidade critical"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Metadados", exact: true }).click();
  await expect(page.locator(".json-document")).toContainText("lab_demo.v1");
  await page.screenshot({
    path: "test-results/artifacts-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Fechar painel" }).click();
  await page.screenshot({
    path: "test-results/canvas-desktop.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("Health and Audit stay active and animate only their own routes", async ({
  page,
}) => {
  await expect(page.locator(".flow-pulse")).toHaveCount(0);
  await expect(
    page.getByRole("article", { name: "Componente Simulação Aplicação" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Iniciar consultas", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Tentar DROP", exact: false }),
  ).toBeDisabled();

  await page
    .getByRole("button", { name: "Ativar Health Check", exact: true })
    .click();
  await page.getByLabel("Digite sakila para confirmar").fill("sakila");
  await page.getByRole("button", { name: "Ativar serviço" }).click();
  await expect(
    page.getByRole("article", { name: "Agente Health Check" }),
  ).toContainText("Executando");
  await expect(page.getByTestId("active-flow-health-check-mcp")).toBeVisible();
  await expect(page.locator(".flow-pulse")).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "Iniciar consultas", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Tentar DROP", exact: false }),
  ).toBeDisabled();

  await page.getByRole("button", { name: "Ativar Audit", exact: true }).click();
  await page.getByLabel("Digite sakila para confirmar").fill("sakila");
  await page.getByRole("button", { name: "Ativar serviço" }).click();
  await expect(
    page.getByRole("article", { name: "Agente Audit Security" }),
  ).toContainText("Executando");
  await expect(page.getByTestId("active-flow-audit-mcp")).toBeVisible();
  await expect(page.locator(".flow-pulse")).toHaveCount(2);
  await expect(
    page.getByRole("button", { name: "Tentar DROP", exact: false }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Tentar ALTER", exact: false }),
  ).toBeEnabled();

  await page.getByRole("button", { name: "Iniciar consultas" }).click();
  await page.getByLabel("Digite sakila para confirmar").fill("sakila");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Iniciar consultas" })
    .click();
  await expect(
    page.getByTestId("active-flow-health-check-simulation"),
  ).toBeVisible();
  await page.getByLabel("Cancelar consultas").click();
  await expect(
    page.getByTestId("active-flow-health-check-simulation"),
  ).toHaveCount(0, { timeout: 30000 });

  await page.getByRole("button", { name: "Tentar DROP", exact: true }).click();
  await page.getByLabel("Digite sakila para confirmar").fill("sakila");
  await page.getByRole("button", { name: "Executar comando" }).click();
  await expect(page.getByTestId("active-flow-audit-simulation")).toBeVisible();
  await page.getByLabel("Cancelar Tentar DROP").click();
  await expect(page.getByTestId("active-flow-audit-simulation")).toHaveCount(
    0,
    { timeout: 30000 },
  );

  await page.getByRole("button", { name: "Tentar ALTER", exact: true }).click();
  await page.getByLabel("Digite sakila para confirmar").fill("sakila");
  await page.getByRole("button", { name: "Executar comando" }).click();
  await expect(page.getByTestId("active-flow-audit-simulation")).toBeVisible();
  await page.getByLabel("Cancelar Tentar ALTER").click();
  await expect(page.getByTestId("active-flow-audit-simulation")).toHaveCount(
    0,
    { timeout: 30000 },
  );

  await page
    .getByRole("article", { name: "Agente Health Check" })
    .getByRole("button", { name: "Parar", exact: true })
    .click();
  await expect(page.getByTestId("active-flow-health-check-mcp")).toHaveCount(0);
  await expect(page.getByTestId("active-flow-audit-mcp")).toBeVisible();
  await expect(page.locator(".flow-pulse")).toHaveCount(1);

  await page
    .getByRole("article", { name: "Agente Audit Security" })
    .getByRole("button", { name: "Parar", exact: true })
    .click();
  await expect(page.locator(".flow-pulse")).toHaveCount(0);
});

test("DBA incident center is limited to received alert files", async ({
  page,
}) => {
  await page.getByRole("button", { name: "Chat do DBA", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "O que você deseja analisar?" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Alertas/ }).click();
  await expect(
    page.getByRole("heading", { name: "Central de ocorrências do DBA" }),
  ).toBeVisible();
  await expect(page.getByText("Nenhuma ocorrência recebida")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Propor ação", exact: true }),
  ).toHaveCount(0);
  await page.screenshot({
    path: "test-results/dba-chat-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Fechar painel" }).click();
});

test("tablet, keyboard, reduced motion and accessible main screen", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1024, height: 1100 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(page.getByTestId("canvas")).toBeVisible();
  await expect
    .poll(async () =>
      page.locator(".canvas").evaluate((container) => {
        const area = container.getBoundingClientRect();
        return Array.from(container.querySelectorAll(".agent-node")).every(
          (node) => {
            const box = node.getBoundingClientRect();
            return (
              box.left >= area.left &&
              box.right <= area.right &&
              box.top >= area.top &&
              box.bottom <= area.bottom
            );
          },
        );
      }),
    )
    .toBeTruthy();
  expect(
    await page.locator(".agent-node").evaluateAll((nodes) => {
      const boxes = nodes.map((node) => node.getBoundingClientRect());
      return boxes.every((box, index) =>
        boxes
          .slice(index + 1)
          .every(
            (other) =>
              box.right <= other.left ||
              other.right <= box.left ||
              box.bottom <= other.top ||
              other.bottom <= box.top,
          ),
      );
    }),
  ).toBeTruthy();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => document.activeElement?.tagName)).toBe(
    "BUTTON",
  );
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(results.violations).toEqual([]);
  await page.screenshot({
    path: "test-results/canvas-tablet.png",
    fullPage: true,
  });
});

test("audit lifecycle, gated scenarios, stop and direct access after reload", async ({
  page,
}) => {
  await page.getByRole("button", { name: "Ativar Audit", exact: true }).click();
  await page.getByLabel("Digite sakila para confirmar").fill("sakila");
  await page.getByRole("button", { name: "Ativar serviço" }).click();
  await expect(
    page.getByRole("button", { name: "Tentar ALTER", exact: false }),
  ).toBeEnabled();
  await page
    .getByRole("article", { name: "Agente Audit Security" })
    .getByRole("button", { name: "Parar", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Tentar ALTER", exact: false }),
  ).toBeDisabled();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Laboratório de agentes" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Sair", exact: true }),
  ).toHaveCount(0);
});

test("HTML evidence blocks scripts and remote resources", async ({ page }) => {
  const blocked: string[] = [];
  const completed: string[] = [];
  page.on("requestfailed", (request) => {
    if (request.url().includes("evil.invalid"))
      blocked.push(request.failure()?.errorText || "unknown");
  });
  page.on("requestfinished", (request) => {
    if (request.url().includes("evil.invalid")) completed.push(request.url());
  });
  await page.route("**/api/artifacts/*/html?*", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: '<html><body>Sandbox fixture<script>parent.document.body.dataset.compromised="true"</script><img src="https://evil.invalid/tracker"><form action="https://evil.invalid/"><button>Submit</button></form></body></html>',
    }),
  );
  await page
    .getByRole("button", { name: "Biblioteca de evidências", exact: true })
    .click();
  await expect(
    page.frameLocator("iframe").getByText("Sandbox fixture"),
  ).toBeVisible();
  expect(
    await page.locator("body").getAttribute("data-compromised"),
  ).toBeNull();
  // Chromium emits a request event even when CSP prevents network access.
  await expect.poll(() => blocked).toEqual([expect.stringMatching(/csp/i)]);
  expect(completed).toEqual([]);
});

test("unavailable reports remain explicit", async ({ page }) => {
  await page.route("**/api/artifacts/*/html?*", (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: '{"detail":"artifact_unavailable"}',
    }),
  );
  await page.reload();
  await page
    .getByRole("button", { name: "Biblioteca de evidências", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText(
    "Evidência indisponível ou substituída",
  );
  await expect(page.locator("iframe")).toHaveCount(0);
});
