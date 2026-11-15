"use strict";
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
