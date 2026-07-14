const uploadForm = document.querySelector("#uploadForm");
const orderFile = document.querySelector("#orderFile");
const fileName = document.querySelector("#fileName");
const dropZone = document.querySelector("#dropZone");
const analyzeButton = document.querySelector("#analyzeButton");
const resetButton = document.querySelector("#resetButton");

const message = document.querySelector("#message");
const planSection = document.querySelector("#planSection");
const modeBadge = document.querySelector("#modeBadge");
const printerBadge = document.querySelector("#printerBadge");
const sessionStatus = document.querySelector("#sessionStatus");

const countryValue = document.querySelector("#countryValue");
const total45Value = document.querySelector("#total45Value");
const total110Value = document.querySelector("#total110Value");
const totalValue = document.querySelector("#totalValue");

const jobCount45 = document.querySelector("#jobCount45");
const jobCount110 = document.querySelector("#jobCount110");
const jobs45Table = document.querySelector("#jobs45Table");
const jobs110Table = document.querySelector("#jobs110Table");

const skippedDetails = document.querySelector("#skippedDetails");
const skippedCount = document.querySelector("#skippedCount");
const skippedList = document.querySelector("#skippedList");

const invalidDetails = document.querySelector("#invalidDetails");
const invalidCount = document.querySelector("#invalidCount");
const invalidList = document.querySelector("#invalidList");

const print45Button = document.querySelector("#print45Button");
const printInstruction = document.querySelector("#printInstruction");

const progressBar = document.querySelector("#progressBar");
const progressText = document.querySelector("#progressText");

const rollDialog = document.querySelector("#rollDialog");
const confirmRollButton =
    document.querySelector("#confirmRollButton");

const failureDialog =
    document.querySelector("#failureDialog");

const failedProduct =
    document.querySelector("#failedProduct");

const failedFormat =
    document.querySelector("#failedFormat");

const failedQuantity =
    document.querySelector("#failedQuantity");

const failedError =
    document.querySelector("#failedError");

const retryButton =
    document.querySelector("#retryButton");

const abortButton =
    document.querySelector("#abortButton");

let lastStatus = null;
let pollingTimer = null;
let testMode = true;


async function apiRequest(url, options = {}) {
    const response = await fetch(url, options);

    let data;

    try {
        data = await response.json();
    } catch {
        throw new Error(
            `Serwer zwrócił nieprawidłową odpowiedź (${response.status}).`
        );
    }

    if (!response.ok || data.ok === false) {
        throw new Error(
            data.error ||
            `Błąd serwera (${response.status}).`
        );
    }

    return data;
}


function showMessage(text, type = "info") {
    message.textContent = text;
    message.className = `message ${type}`;
    message.hidden = false;
}


function hideMessage() {
    message.hidden = true;
    message.textContent = "";
}


function setLoading(button, loading, loadingText) {
    if (loading) {
        button.dataset.originalText =
            button.textContent;

        button.textContent = loadingText;
        button.disabled = true;
    } else {
        button.textContent =
            button.dataset.originalText ||
            button.textContent;

        button.disabled = false;
    }
}


function positionLabel(count) {
    if (count === 1) {
        return "1 pozycja";
    }

    if (count >= 2 && count <= 4) {
        return `${count} pozycje`;
    }

    return `${count} pozycji`;
}


function renderJobs(tableBody, jobs) {
    tableBody.innerHTML = "";

    if (!jobs.length) {
        const row = document.createElement("tr");

        row.innerHTML = `
            <td colspan="2" class="empty-row">
                Brak etykiet w tym formacie
            </td>
        `;

        tableBody.appendChild(row);
        return;
    }

    for (const job of jobs) {
        const row = document.createElement("tr");
        const productCell = document.createElement("td");
        const quantityCell = document.createElement("td");

        productCell.textContent = job.product;
        quantityCell.textContent = job.quantity;

        row.append(productCell, quantityCell);
        tableBody.appendChild(row);
    }
}


function renderItems(
    listElement,
    items,
    errorMode = false
) {
    listElement.innerHTML = "";

    for (const entry of items) {
        const item = document.createElement("li");

        if (errorMode) {
            item.textContent =
                `${entry.product}: ${entry.error}`;
        } else {
            item.textContent =
                `${entry.product} — ${entry.quantity} szt.`;
        }

        listElement.appendChild(item);
    }
}


function renderPlan(plan) {
    countryValue.textContent = plan.country;
    total45Value.textContent = plan.total_45x45;
    total110Value.textContent = plan.total_45x110;
    totalValue.textContent = plan.total_labels;

    jobCount45.textContent =
        positionLabel(plan.jobs_45x45.length);

    jobCount110.textContent =
        positionLabel(plan.jobs_45x110.length);

    renderJobs(jobs45Table, plan.jobs_45x45);
    renderJobs(jobs110Table, plan.jobs_45x110);

    const skipped = plan.skipped_items || [];

    skippedCount.textContent = skipped.length;
    skippedDetails.hidden = skipped.length === 0;
    renderItems(skippedList, skipped);

    const invalid = plan.invalid_items || [];

    invalidCount.textContent = invalid.length;
    invalidDetails.hidden = invalid.length === 0;
    renderItems(invalidList, invalid, true);

    planSection.hidden = false;
    resetButton.hidden = false;
    print45Button.disabled = invalid.length > 0;
}


function renderPrinter(printerData) {
    const selected = printerData?.selected;

    printerBadge.className = "badge printer-badge";

    if (selected) {
        if (selected.has_warning) {
            printerBadge.textContent =
                `Drukarka: ${selected.name} — uwaga`;

            printerBadge.classList.add(
                "printer-warning"
            );

            printerBadge.title =
                selected.warning ||
                "Drukarka zgłasza ostrzeżenie";

            return;
        }

        printerBadge.textContent =
            `Drukarka: ${selected.name}`;

        printerBadge.classList.add(
            "printer-found"
        );

        printerBadge.title =
            `Port: ${selected.port_name}. ` +
            "Kliknij, aby sprawdzić ponownie.";

        return;
    }

    if (testMode) {
        printerBadge.textContent =
            "Drukarka: niewymagana w teście";

        printerBadge.classList.add(
            "printer-test"
        );

        printerBadge.title =
            "Tryb testowy nie wymaga drukarki.";

        return;
    }

    printerBadge.textContent =
        "Drukarka TSC: nieznaleziona";

    printerBadge.classList.add(
        "printer-missing"
    );

    printerBadge.title =
        "Kliknij, aby ponownie wyszukać drukarkę.";
}


function updateProgress(progress) {
    const safeProgress = progress || {};

    const completed =
        Number(safeProgress.completed_total || 0);

    const total =
        Number(safeProgress.total_jobs || 0);

    const percent = total > 0
        ? Math.min(100, (completed / total) * 100)
        : 0;

    progressBar.style.width = `${percent}%`;
    progressText.textContent =
        `${completed} z ${total} pozycji`;
}


function setSessionStatus(status) {
    const labels = {
        no_order: "Brak zamówienia",
        ready: "Gotowe do druku",
        printing_45x45: "Drukowanie 45×45",
        waiting_for_110:
            "Oczekiwanie na zmianę rolki",
        printing_45x110: "Drukowanie 45×110",
        completed: "Zamówienie zakończone",
        failed: "Błąd drukowania",
        aborted: "Zamówienie przerwane",
    };

    sessionStatus.textContent =
        labels[status] || status;

    sessionStatus.className = "status-badge";

    if (
        status === "printing_45x45" ||
        status === "printing_45x110"
    ) {
        sessionStatus.classList.add("running");
    } else if (status === "waiting_for_110") {
        sessionStatus.classList.add("waiting");
    } else if (status === "completed") {
        sessionStatus.classList.add("completed");
    } else if (
        status === "failed" ||
        status === "aborted"
    ) {
        sessionStatus.classList.add("failed");
    }
}


function showFailure(session) {
    const failedJob = session.failed_job || {};

    failedProduct.textContent =
        failedJob.product_folder_name || "Nieznany";

    failedFormat.textContent =
        failedJob.stage || "Nieznany";

    failedQuantity.textContent =
        failedJob.quantity ?? "—";

    failedError.textContent =
        failedJob.error ||
        session.error ||
        "Nieznany błąd drukowania.";

    if (!failureDialog.open) {
        failureDialog.showModal();
    }
}


function updateInterface(session) {
    const status = session.status || "no_order";

    setSessionStatus(status);
    updateProgress(session.progress);

    const printing =
        status === "printing_45x45" ||
        status === "printing_45x110" ||
        session.worker_running;

    print45Button.disabled =
        printing || status !== "ready";

    confirmRollButton.disabled =
        printing || status !== "waiting_for_110";

    retryButton.disabled =
        printing || status !== "failed";

    abortButton.disabled =
        printing || status !== "failed";

    if (status === "ready") {
        printInstruction.textContent =
            "Najpierw zostaną wydrukowane wszystkie etykiety 45×45.";
    }

    if (status === "printing_45x45") {
        printInstruction.textContent =
            "Trwa drukowanie etykiet 45×45…";

        if (failureDialog.open) {
            failureDialog.close();
        }
    }

    if (status === "waiting_for_110") {
        printInstruction.textContent =
            "Etykiety 45×45 zakończone. Zmień rolkę na 45×110.";

        if (!rollDialog.open) {
            rollDialog.showModal();
        }
    }

    if (status === "printing_45x110") {
        printInstruction.textContent =
            "Trwa drukowanie etykiet 45×110…";

        if (rollDialog.open) {
            rollDialog.close();
        }

        if (failureDialog.open) {
            failureDialog.close();
        }
    }

    if (status === "failed") {
        if (rollDialog.open) {
            rollDialog.close();
        }

        printInstruction.textContent =
            "Drukowanie zatrzymane. Usuń przyczynę błędu i ponów etykietę.";

        showFailure(session);

        if (lastStatus !== "failed") {
            showMessage(
                session.error ||
                "Drukowanie zostało zatrzymane.",
                "error"
            );
        }
    }

    if (status === "completed") {
        printInstruction.textContent =
            "Wszystkie etykiety zostały wysłane do drukarki.";

        if (rollDialog.open) {
            rollDialog.close();
        }

        if (failureDialog.open) {
            failureDialog.close();
        }

        if (lastStatus !== "completed") {
            showMessage(
                "Zamówienie zostało zakończone.",
                "success"
            );
        }
    }

    if (status === "aborted") {
        printInstruction.textContent =
            "Zamówienie zostało przerwane przez operatora.";

        if (rollDialog.open) {
            rollDialog.close();
        }

        if (failureDialog.open) {
            failureDialog.close();
        }

        if (lastStatus !== "aborted") {
            showMessage(
                "Zamówienie zostało przerwane.",
                "error"
            );
        }
    }

    lastStatus = status;
}


async function refreshStatus() {
    try {
        const data = await apiRequest("/api/status");

        if (data.plan && planSection.hidden) {
            renderPlan(data.plan);
        }

        updateInterface(data.session);
    } catch (error) {
        console.error(error);
    }
}


async function refreshPrinter(showResult = false) {
    printerBadge.disabled = true;
    printerBadge.textContent =
        "Sprawdzanie drukarki…";

    try {
        const data = await apiRequest(
            "/api/printer"
        );

        testMode = Boolean(data.test_mode);
        renderPrinter(data.printer);

        if (showResult) {
            if (data.printer.selected) {
                showMessage(
                    `Znaleziono drukarkę: ${data.printer.selected.name}`,
                    "success"
                );
            } else if (testMode) {
                showMessage(
                    "Nie znaleziono drukarki TSC. W trybie testowym drukarka nie jest wymagana.",
                    "info"
                );
            } else {
                showMessage(
                    "Nie znaleziono drukarki TSC MH640/MH641.",
                    "error"
                );
            }
        }
    } catch (error) {
        printerBadge.textContent =
            "Błąd sprawdzania drukarki";

        printerBadge.className =
            "badge printer-badge printer-missing";

        if (showResult) {
            showMessage(error.message, "error");
        }
    } finally {
        printerBadge.disabled = false;
    }
}


function startPolling() {
    if (pollingTimer !== null) {
        return;
    }

    pollingTimer = window.setInterval(
        refreshStatus,
        1000
    );
}


orderFile.addEventListener("change", () => {
    if (orderFile.files.length) {
        fileName.textContent =
            orderFile.files[0].name;
    }
});


for (const eventName of ["dragenter", "dragover"]) {
    dropZone.addEventListener(eventName, event => {
        event.preventDefault();
        dropZone.classList.add("dragging");
    });
}


for (const eventName of ["dragleave", "drop"]) {
    dropZone.addEventListener(eventName, event => {
        event.preventDefault();
        dropZone.classList.remove("dragging");
    });
}


dropZone.addEventListener("drop", event => {
    const files = event.dataTransfer.files;

    if (files.length) {
        orderFile.files = files;
        fileName.textContent = files[0].name;
    }
});


uploadForm.addEventListener("submit", async event => {
    event.preventDefault();
    hideMessage();

    if (!orderFile.files.length) {
        showMessage(
            "Wybierz plik zamówienia.",
            "error"
        );
        return;
    }

    const formData = new FormData();

    formData.append(
        "file",
        orderFile.files[0]
    );

    setLoading(
        analyzeButton,
        true,
        "Analizowanie…"
    );

    try {
        const data = await apiRequest(
            "/api/analyze",
            {
                method: "POST",
                body: formData,
            }
        );

        renderPlan(data.plan);
        updateInterface(data.session);

        if (data.printer) {
            renderPrinter(data.printer);
        }

        showMessage(
            "Zamówienie zostało poprawnie przeanalizowane.",
            "success"
        );

        startPolling();
    } catch (error) {
        showMessage(error.message, "error");
    } finally {
        setLoading(analyzeButton, false);
    }
});


print45Button.addEventListener("click", async () => {
    hideMessage();

    setLoading(
        print45Button,
        true,
        "Uruchamianie…"
    );

    try {
        await apiRequest(
            "/api/print/45x45",
            { method: "POST" }
        );

        showMessage(
            "Rozpoczęto drukowanie etykiet 45×45.",
            "info"
        );

        await refreshStatus();
    } catch (error) {
        showMessage(error.message, "error");
    } finally {
        setLoading(print45Button, false);
    }
});


confirmRollButton.addEventListener(
    "click",
    async () => {
        hideMessage();

        setLoading(
            confirmRollButton,
            true,
            "Uruchamianie druku…"
        );

        try {
            await apiRequest(
                "/api/print/45x110",
                { method: "POST" }
            );

            if (rollDialog.open) {
                rollDialog.close();
            }

            showMessage(
                "Rozpoczęto drukowanie etykiet 45×110.",
                "info"
            );

            await refreshStatus();
        } catch (error) {
            showMessage(error.message, "error");
        } finally {
            setLoading(
                confirmRollButton,
                false
            );
        }
    }
);


retryButton.addEventListener("click", async () => {
    hideMessage();

    setLoading(
        retryButton,
        true,
        "Ponawianie…"
    );

    try {
        await apiRequest(
            "/api/print/retry",
            { method: "POST" }
        );

        if (failureDialog.open) {
            failureDialog.close();
        }

        showMessage(
            "Ponowiono drukowanie od błędnej etykiety.",
            "info"
        );

        await refreshStatus();
    } catch (error) {
        showMessage(error.message, "error");
    } finally {
        setLoading(retryButton, false);
    }
});


abortButton.addEventListener("click", async () => {
    hideMessage();

    setLoading(
        abortButton,
        true,
        "Przerywanie…"
    );

    try {
        const data = await apiRequest(
            "/api/print/abort",
            { method: "POST" }
        );

        if (failureDialog.open) {
            failureDialog.close();
        }

        updateInterface(data.session);

        showMessage(
            "Zamówienie zostało przerwane.",
            "error"
        );
    } catch (error) {
        showMessage(error.message, "error");
    } finally {
        setLoading(abortButton, false);
    }
});


resetButton.addEventListener("click", async () => {
    try {
        await apiRequest(
            "/api/reset",
            { method: "POST" }
        );

        if (rollDialog.open) {
            rollDialog.close();
        }

        if (failureDialog.open) {
            failureDialog.close();
        }

        uploadForm.reset();

        fileName.textContent =
            "lub przeciągnij go w to miejsce";

        planSection.hidden = true;
        resetButton.hidden = true;

        progressBar.style.width = "0%";
        progressText.textContent = "0 z 0 pozycji";

        hideMessage();
        lastStatus = null;
    } catch (error) {
        showMessage(error.message, "error");
    }
});


printerBadge.addEventListener("click", () => {
    refreshPrinter(true);
});


async function loadConfiguration() {
    try {
        const config = await apiRequest(
            "/api/config"
        );

        testMode = Boolean(config.test_mode);

        if (testMode) {
            modeBadge.textContent =
                "TRYB TESTOWY — bez drukowania";

            modeBadge.className = "badge test";
        } else {
            modeBadge.textContent =
                "TRYB PRODUKCYJNY";

            modeBadge.className =
                "badge production";
        }

        renderPrinter(config.printer);

        if (!config.etilabel_found) {
            showMessage(
                "Nie znaleziono programu ETILABEL.",
                "error"
            );
        }
    } catch (error) {
        modeBadge.textContent =
            "Błąd konfiguracji";

        modeBadge.className = "badge test";
        showMessage(error.message, "error");
    }
}


loadConfiguration();
refreshStatus();
startPolling();