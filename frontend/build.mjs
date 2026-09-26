// Vercel 빌드: 정적 파일을 dist/로 복사하고 API_BASE_URL 환경 변수로 config.js를 생성한다.
import { cp, mkdir, writeFile } from "node:fs/promises";

const url = (process.env.API_BASE_URL || "").trim().replace(/\/$/, "");
if (!/^https?:\/\/[^/]+$/.test(url)) {
  throw new Error("API_BASE_URL 환경 변수가 필요합니다 (예: https://summer-seoul-chat.onrender.com)");
}

await mkdir("dist", { recursive: true });
for (const file of ["index.html", "styles.css", "app.js"]) await cp(file, `dist/${file}`);
await writeFile("dist/config.js", `window.API_BASE_URL = ${JSON.stringify(url)};\n`);
console.log(`built dist/ with API_BASE_URL=${url}`);
