import { ReplitConnectors } from "@replit/connectors-sdk";

const input = await new Promise((resolve, reject) => {
  let data = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => { data += chunk; });
  process.stdin.on("end", () => resolve(data));
  process.stdin.on("error", reject);
});

const request = JSON.parse(input || "{}");
const connectors = new ReplitConnectors();
const response = await connectors.proxy("github", request.path, {
  method: request.method || "GET",
  ...(request.body === undefined ? {} : {
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request.body),
  }),
});

const text = await response.text();
let body;
try {
  body = text ? JSON.parse(text) : null;
} catch {
  body = { message: text.slice(0, 2000) };
}

process.stdout.write(JSON.stringify({
  ok: response.ok,
  status: response.status,
  body,
}));