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

async function copyText(text) {
    if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return;
    }
    const field = document.createElement("textarea");
    field.value = text;
    field.readOnly = true;
    field.style.cssText = "position:fixed;opacity:0;pointer-events:none";
    const focused = document.activeElement;
    document.body.append(field);
    try {
        field.select();
        if (!document.execCommand("copy")) throw new Error("Copy unavailable");
    } finally {
        field.remove();
        focused?.focus();
    }
}

const copyResets = new WeakMap();
document.addEventListener("click", async (event) => {
    const button = event.target.closest(".mr-copy");
    if (!button) return;
    const status = document.getElementById("copy-status");
    button.dataset.copyLabel ||= button.textContent;
    clearTimeout(copyResets.get(button));
    try {
        await copyText(button.dataset.copy);
        button.textContent = "Copied";
        if (status) status.textContent = button.dataset.copyMessage || "Payment detail copied to clipboard.";
    } catch (_) {
        if (status) status.textContent = "Copy unavailable. Allow clipboard access in your browser and try again.";
    } finally {
        copyResets.set(button, setTimeout(() => { button.textContent = button.dataset.copyLabel; }, 1800));
    }
});

// Keep cents exact when adding 100m, including for values above Number.MAX_SAFE_INTEGER.
document.querySelectorAll("input[data-bid-step]").forEach((input) => {
    const cents = (raw) => {
        if (!/^\d+(\.\d{0,2})?$/.test(raw)) return null;
        const [whole, fraction = ""] = raw.split(".");
        return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0"));
    };
    const adjust = (direction) => {
        const current = cents(input.value || "0");
        if (current === null) return;
        const minimum = cents(input.min) || 1n;
        const result = current + BigInt(direction) * 10000000000n;
        const value = result < minimum ? minimum : result;
        input.value = `${value / 100n}.${String(value % 100n).padStart(2, "0")}`;
        input.dispatchEvent(new Event("input", { bubbles: true }));
    };
    input.addEventListener("keydown", (event) => {
        if (event.key === "ArrowUp" || event.key === "ArrowDown") {
            event.preventDefault();
            adjust(event.key === "ArrowUp" ? 1 : -1);
        }
    });
    input.addEventListener("wheel", (event) => {
        if (!event.deltaY) return;
        event.preventDefault();
        adjust(event.deltaY < 0 ? 1 : -1);
    }, { passive: false });
    input.closest("form").querySelectorAll("[data-bid-adjust]").forEach((button) => {
        button.addEventListener("click", () => adjust(Number(button.dataset.bidAdjust)));
    });
});
