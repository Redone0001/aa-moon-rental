"use strict";

// The preview is deliberately separate from the input: never round or rewrite
// the original decimal string submitted to Django.
(() => {
    const formatter = new Intl.NumberFormat("fr-BE", { maximumFractionDigits: 2 });
    const units = [[1e12, "t"], [1e9, "b"], [1e6, "m"], [1e3, "k"]];
    const refreshers = [];

    document.querySelectorAll("input[data-isk-input]").forEach((input) => {
        const preview = document.createElement("output");
        preview.id = `${input.id}-isk-preview`;
        preview.htmlFor = input.id;
        preview.className = "form-text text-info mr-numeric mr-isk-preview";
        preview.setAttribute("aria-live", "polite");
        preview.setAttribute("aria-atomic", "true");
        (input.closest(".input-group") || input).after(preview);
        const descriptions = new Set((input.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
        descriptions.add(preview.id);
        input.setAttribute("aria-describedby", [...descriptions].join(" "));

        const update = () => {
            const raw = input.value.trim();
            const amount = Number(raw);
            preview.hidden = raw === "" || !Number.isFinite(amount) || amount < 0;
            if (preview.hidden) {
                preview.textContent = "";
                return;
            }
            const [scale, suffix] = units.find(([threshold]) => amount >= threshold) || [1, ""];
            preview.textContent = `${formatter.format(amount / scale)}${suffix ? ` ${suffix}` : ""} ISK`;
        };
        input.addEventListener("input", update);
        input.addEventListener("change", update);
        refreshers.push(update);
        update();
    });

    const refresh = () => refreshers.forEach((update) => update());
    window.addEventListener("pageshow", refresh);
    document.addEventListener("reset", () => setTimeout(refresh, 0));
})();

document.querySelectorAll("[data-moon-selector]").forEach((selector) => {
    const form = selector.closest("form");
    const counter = form.querySelector("[data-moon-selection-count]");
    const multiple = selector.dataset.multiple === "true";
    const update = () => {
        const checked = selector.querySelectorAll("input:checked");
        if (multiple) {
            const kept = JSON.parse(selector.querySelector('input[name$="_kept"]').value);
            counter.textContent = new Set([...kept, ...[...checked].map((input) => input.value)]).size;
        } else {
            const previous = selector.querySelector('input[type="hidden"]');
            counter.textContent = checked.length || (previous ? 1 : 0);
            const outside = form.querySelector("[data-moon-outside-selection]");
            if (outside) outside.hidden = checked.length > 0;
        }
    };
    selector.addEventListener("change", update);
    window.addEventListener("pageshow", update);
    update();
});

document.addEventListener("click", async (event) => {
    const button = event.target.closest(".mr-copy");
    if (!button) return;
    const status = document.getElementById("copy-status");
    try {
        await navigator.clipboard.writeText(button.dataset.copy);
        button.textContent = "Copied";
        if (status) status.textContent = "Payment detail copied to clipboard.";
        setTimeout(() => { button.textContent = "Copy"; }, 1800);
    } catch (_) {
        if (status) status.textContent = "Copy unavailable. Select and copy the payment detail manually.";
    }
});
