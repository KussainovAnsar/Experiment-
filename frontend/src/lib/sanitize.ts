/**
 * Minimal sanitiser for Telegram-style HTML (aiogram-compatible subset).
 * Allowed: <b>, <strong>, <i>, <em>, <u>, <s>, <code>, <pre>, <a href="http(s)://…">, <br>.
 * Everything else is escaped as text; dangerous attributes/protocols are dropped.
 */

const ALLOWED_TAGS = new Set(["b", "strong", "i", "em", "u", "s", "code", "pre", "a", "br"]);
const SAFE_HREF = /^(https?:\/\/|tg:\/\/|mailto:)/i;

function escapeText(text: string): string {
  return text
    .replace(/&(?!(amp|lt|gt|quot|#39|#x27|nbsp);)/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function escapeAttr(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

const TAG_RE = /<\/?([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>/g;

/**
 * Sanitise Telegram HTML and convert newlines to <br>.
 * The result is safe to inject via dangerouslySetInnerHTML.
 */
export function sanitizeTelegramHtml(input: string | null | undefined): string {
  if (!input) return "";
  let out = "";
  let last = 0;
  const openStack: string[] = [];
  for (const match of input.matchAll(TAG_RE)) {
    const [raw, tagNameRaw, attrs] = match;
    const start = match.index ?? 0;
    out += escapeText(input.slice(last, start));
    last = start + raw.length;
    const tag = (tagNameRaw ?? "").toLowerCase();
    const isClosing = raw.startsWith("</");
    if (!ALLOWED_TAGS.has(tag)) {
      // Unknown tag: render as escaped text so nothing is silently lost.
      out += escapeText(raw);
      continue;
    }
    if (tag === "br") {
      out += "<br>";
      continue;
    }
    if (isClosing) {
      const idx = openStack.lastIndexOf(tag);
      if (idx === -1) continue; // stray closing tag
      // Close everything opened after it (auto-balance).
      while (openStack.length > idx) {
        out += `</${openStack.pop()}>`;
      }
      continue;
    }
    if (tag === "a") {
      const hrefMatch = /href\s*=\s*("([^"]*)"|'([^']*)'|([^\s"'>]+))/i.exec(attrs ?? "");
      const href = hrefMatch?.[2] ?? hrefMatch?.[3] ?? hrefMatch?.[4] ?? "";
      if (href && SAFE_HREF.test(href.trim())) {
        out += `<a href="${escapeAttr(href.trim())}" target="_blank" rel="noopener noreferrer">`;
      } else {
        out += "<a>";
      }
      openStack.push("a");
      continue;
    }
    out += `<${tag}>`;
    openStack.push(tag);
  }
  out += escapeText(input.slice(last));
  while (openStack.length) {
    out += `</${openStack.pop()}>`;
  }
  return out.replace(/\r?\n/g, "<br>");
}

/** Strip all tags (for plain-text previews / length counters). */
export function stripTags(input: string | null | undefined): string {
  if (!input) return "";
  return input
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&");
}
