import { describe, expect, it } from "vitest";
import { sanitizeTelegramHtml, stripTags } from "./sanitize";

describe("sanitizeTelegramHtml", () => {
  it("keeps allowed Telegram tags", () => {
    expect(sanitizeTelegramHtml("<b>Тема:</b> падежи <i>сегодня</i>")).toBe("<b>Тема:</b> падежи <i>сегодня</i>");
  });

  it("drops script tags and event handlers", () => {
    const out = sanitizeTelegramHtml('<script>alert(1)</script><b onclick="x()">жирный</b>');
    expect(out).not.toContain("<script");
    expect(out).not.toContain("onclick");
    expect(out).toContain("<b>жирный</b>");
  });

  it("allows only safe hrefs and adds rel/target", () => {
    expect(sanitizeTelegramHtml('<a href="https://example.kz/p/abc">кабинет</a>')).toBe(
      '<a href="https://example.kz/p/abc" target="_blank" rel="noopener noreferrer">кабинет</a>',
    );
    expect(sanitizeTelegramHtml('<a href="javascript:alert(1)">x</a>')).toBe("<a>x</a>");
  });

  it("escapes stray angle brackets and balances tags", () => {
    expect(sanitizeTelegramHtml("1 < 2 и <b>не закрыто")).toBe("1 &lt; 2 и <b>не закрыто</b>");
    expect(sanitizeTelegramHtml("<div>блок</div>")).toBe("&lt;div&gt;блок&lt;/div&gt;");
  });

  it("converts newlines to <br>", () => {
    expect(sanitizeTelegramHtml("строка 1\nстрока 2")).toBe("строка 1<br>строка 2");
  });

  it("returns empty string for empty input", () => {
    expect(sanitizeTelegramHtml(null)).toBe("");
    expect(sanitizeTelegramHtml("")).toBe("");
  });
});

describe("stripTags", () => {
  it("removes tags and decodes entities", () => {
    expect(stripTags("<b>Тема</b>: &lt;падежи&gt;<br>дальше")).toBe("Тема: <падежи>\nдальше");
  });
});
