// Injected into an application page: finds every fillable question, tags its
// control with data-jr-id so the filler can target it, and returns a JSON
// description {id, label, kind, required, options, value}.
// kind: text | textarea | email | tel | url | number | date | select |
//       radio | checkbox_group | checkbox | combobox | yesno | file
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return s.visibility !== "hidden" && s.display !== "none" && (r.width > 0 || r.height > 0);
  };
  const clean = (t) => (t || "").replace(/\s+/g, " ").replace(/\s*\*\s*$/, "").trim();
  const textOf = (el) => clean(el && (el.innerText || el.textContent));

  const labelFor = (el) => {
    if (el.labels && el.labels.length) return textOf(el.labels[0]);
    const by = el.getAttribute("aria-labelledby");
    if (by) {
      const t = by.split(/\s+/).map((id) => textOf(document.getElementById(id))).join(" ");
      if (clean(t)) return clean(t);
    }
    if (el.getAttribute("aria-label")) return clean(el.getAttribute("aria-label"));
    const fs = el.closest("fieldset");
    if (fs && fs.querySelector("legend")) return textOf(fs.querySelector("legend"));
    // walk up: first ancestor that holds a label-ish element before the control
    let node = el.parentElement;
    for (let i = 0; i < 6 && node; i++, node = node.parentElement) {
      const lab = node.querySelector("label, legend, [class*='label' i], [class*='question' i]");
      if (lab && !lab.contains(el) && clean(textOf(lab))) return textOf(lab);
    }
    return clean(el.getAttribute("placeholder") || el.name || "");
  };
  // Question text for a group of options (radio/checkbox/yes-no): the first
  // line of the nearest ancestor's text that isn't one of the options.
  const NOISE = new Set(["Clear selection", "Yes", "No", "*"]);
  const questionFor = (container, options) => {
    const opts = new Set(options.map(clean));
    let node = container;
    for (let i = 0; i < 6 && node; i++, node = node.parentElement) {
      const lines = (node.innerText || "").split("\n").map(clean)
        .filter((l) => l && !opts.has(l) && !NOISE.has(l));
      if (lines.length) return lines[0];
    }
    return "";
  };
  const commonAncestor = (els) => {
    let a = els[0].parentElement;
    while (a && !els.every((e) => a.contains(e))) a = a.parentElement;
    return a;
  };
  const isRequired = (el, label) =>
    el.required || el.getAttribute("aria-required") === "true" ||
    /\*\s*$/.test((el.labels && el.labels[0] && el.labels[0].innerText) || "") ||
    !!(el.closest("[class*='required' i]"));

  // Clear ids from any previous extract on this page: re-renders between steps
  // otherwise leave stale ids that collide, so answers map to the wrong field
  // (e.g. the phone number landing in the Country dropdown).
  document.querySelectorAll("[data-jr-id]").forEach((e) => e.removeAttribute("data-jr-id"));
  let n = 0;
  const tag = (el) => {
    if (!el.dataset.jrId) el.dataset.jrId = "f" + n++;
    else n = Math.max(n, parseInt(el.dataset.jrId.slice(1)) + 1);
    return el.dataset.jrId;
  };
  const fields = [];
  const groups = new Map();

  const HONEYPOT = /beecatcher|honeypot|^hp_|website_url_confirm/i;
  document.querySelectorAll("input, textarea, select").forEach((el) => {
    const type = (el.type || "").toLowerCase();
    if (HONEYPOT.test(el.getAttribute("data-automation-id") || "") || HONEYPOT.test(el.name || "")) return;
    if (el.closest("[data-automation-id='multiselectInputContainer']")) {
      if (!visible(el)) return;
      const box = el.closest("[data-automation-id^='formField']") || el.parentElement;
      const chosen = [...(box ? box.querySelectorAll("[data-automation-id='selectedItem']") : [])].map(textOf);
      fields.push({ id: tag(el), label: labelFor(el), kind: "wd_multiselect", required: isRequired(el),
                    options: [], value: chosen.join("; ") });
      return;
    }
    const dpart = (el.getAttribute("data-automation-id") || "").match(/^dateSection(Month|Day|Year)-input$/);
    if (["hidden", "submit", "button", "reset", "image", "search", "password"].includes(type)) return;
    if (type !== "file" && !visible(el)) return;
    if (el.closest("[aria-hidden='true']") && type !== "file") return;
    let label = labelFor(el);
    if (dpart) {
      const ff = el.closest("[data-automation-id^='formField']");
      const base = ff ? clean((ff.querySelector("label, legend") || ff).innerText.split("\n")[0]) : label;
      label = `${base} — ${dpart[1] === "Month" ? "month (MM)" : dpart[1] === "Day" ? "day (DD)" : "year (YYYY)"}`;
    }
    if (type === "radio" || (type === "checkbox" && el.name && document.querySelectorAll(`input[type=checkbox][name="${CSS.escape(el.name)}"]`).length > 1)) {
      const key = type + ":" + (el.name || label);
      if (!groups.has(key)) {
        const fs = el.closest("fieldset, [role=radiogroup], [role=group]");
        const g = { id: tag(fs || el), label: fs ? labelFor(fs.querySelector("input")) : label,
                    kind: type === "radio" ? "radio" : "checkbox_group",
                    required: isRequired(el), options: [], optionIds: [], value: [] };
        // group question text: legend, else the label found above the first option
        if (fs && fs.querySelector("legend")) g.label = textOf(fs.querySelector("legend"));
        groups.set(key, g);
        fields.push(g);
      }
      const g = groups.get(key);
      const optLabel = (el.labels && el.labels[0] && textOf(el.labels[0])) || el.value;
      g.options.push(optLabel);
      g.optionIds.push(tag(el));
      if (el.checked) g.value.push(optLabel);
      return;
    }
    if (el.getAttribute("role") === "combobox" || el.closest("[class*='select__control']")) {
      fields.push({ id: tag(el), label, kind: "combobox", required: isRequired(el), options: [],
                    value: textOf(el.closest("[class*='select__control']") || el) || el.value });
      return;
    }
    const kind = el.tagName === "SELECT" ? "select" : el.tagName === "TEXTAREA" ? "textarea"
      : type === "file" ? "file" : type === "checkbox" ? "checkbox"
      : ["email", "tel", "url", "number", "date"].includes(type) ? type : "text";
    const f = { id: tag(el), label, kind, required: isRequired(el), options: [], value: el.value };
    if (kind === "select") {
      f.options = [...el.options].filter((o) => o.value !== "").map((o) => clean(o.text));
      f.value = el.selectedIndex > 0 ? clean(el.options[el.selectedIndex].text) : "";
    }
    if (kind === "checkbox") f.value = el.checked;
    if (kind === "file") {
      const box = el.closest("div, section");
      f.autofill = !!(box && /autofill/i.test(textOf(box.parentElement || box)));
    }
    fields.push(f);
  });

  // header / nav / account-menu chrome that isn't part of the application form
  const CHROME = "[data-automation-id='header'], [data-automation-id='utilityButtonBar'], " +
    "[data-automation-id='navigationContainer'], [data-automation-id='footerContainer'], " +
    "header, nav, [role='banner'], [role='navigation']";
  const CHROME_AID = /^(settings|utilityButton|navigationItem|hammyMenuIcon|changeEmail|personalInformation)/i;

  // The real question for a Workday control: often a block ABOVE the formField,
  // not inside it (the formField's own label is just "Required").
  const NOISE_Q = /^(required|select one|\*|yes|no|not applicable|indicates a required field)$/i;
  const bestQuestion = (node, control) => {
    if (!node) return "";
    const lines = (node.innerText || "").split("\n").map(clean)
      .filter((l) => l && !NOISE_Q.test(l) && !(control && control.innerText && l === clean(control.innerText)));
    const q = lines.find((l) => l.endsWith("?")) || lines.sort((a, b) => b.length - a.length)[0];
    return q || "";
  };
  const wdQuestion = (el) => {
    const ff = el.closest("[data-automation-id^='formField'], [role='group']") || el.parentElement;
    let q = bestQuestion(ff, el);
    if (q) return q;
    for (let node = ff; node && node !== document.body; node = node.parentElement) {
      for (let sib = node.previousElementSibling; sib; sib = sib.previousElementSibling) {
        q = bestQuestion(sib, el);
        if (q) return q;
      }
    }
    return "";
  };

  // Workday single-select dropdowns: <button aria-haspopup="listbox">
  document.querySelectorAll("button[aria-haspopup='listbox']").forEach((b) => {
    if (!visible(b)) return;
    if (b.closest(CHROME) || CHROME_AID.test(b.getAttribute("data-automation-id") || "")) return;
    const v = clean(b.innerText);
    if (/^(settings|personal information|change email|account)$/i.test(v)) return;
    const lbl = labelFor(b).replace(v, "").trim();
    fields.push({ id: tag(b), label: (!lbl || NOISE_Q.test(lbl)) ? wdQuestion(b) : lbl, kind: "wd_select",
                  required: isRequired(b) || /\*/.test(textOf(b.closest("[data-automation-id^='formField']"))),
                  options: [], value: /^select one$/i.test(v) ? "" : v });
  });

  // Ashby-style Yes/No button pairs
  document.querySelectorAll("button").forEach((b) => {
    if (!visible(b) || clean(b.innerText) !== "Yes") return;
    const parent = b.parentElement;
    const btns = [...parent.querySelectorAll("button")].map((x) => clean(x.innerText));
    if (btns.length !== 2 || !btns.includes("No")) return;
    fields.push({ id: tag(parent), label: questionFor(parent, ["Yes", "No"]), kind: "yesno",
                  options: ["Yes", "No"], required: true, value: "" });
  });

  // Re-label option groups with their real question text.
  for (const [key, g] of groups) {
    const inputs = [...document.querySelectorAll(`[data-jr-id]`)]
      .filter((e) => e.tagName === "INPUT" && (key.endsWith(":" + e.name)));
    const scope = inputs.length > 1 ? commonAncestor(inputs) : document.querySelector(`[data-jr-id="${g.id}"]`);
    const legend = scope && scope.closest("fieldset") && scope.closest("fieldset").querySelector("legend");
    g.label = legend ? textOf(legend) : questionFor(scope, g.options) || g.label;
    g.required = g.required || /\*/.test((scope && scope.parentElement && scope.parentElement.innerText || "").split("\n")[0]);
  }
  return fields;
}
