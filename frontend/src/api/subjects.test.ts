import { describe, expect, it } from "vitest";
import { subjectTitle } from "./queries";

describe("subjectTitle", () => {
  const subjects = [
    { code: "russian", title: "Русский язык" },
    { code: "math", title: "Математика" },
  ];
  it("maps a code to the title from GET /api/subjects", () => {
    expect(subjectTitle(subjects, "math")).toBe("Математика");
  });
  it("falls back to the code for unknown codes or a missing list", () => {
    expect(subjectTitle(subjects, "astronomy")).toBe("astronomy");
    expect(subjectTitle(undefined, "russian")).toBe("russian");
  });
});
