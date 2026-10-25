"use strict";

const loginForm = document.querySelector("#login-form");
const passwordInput = document.querySelector("#password");
const submitButton = document.querySelector("#login-submit");
const errorMessage = document.querySelector("#login-error");
const togglePassword = document.querySelector("#toggle-password");

togglePassword.addEventListener("click", () => {
  const visible = passwordInput.type === "password";
  passwordInput.type = visible ? "text" : "password";
  togglePassword.textContent = visible ? "Скрыть" : "Показать";
  togglePassword.setAttribute("aria-label", visible ? "Скрыть пароль" : "Показать пароль");
  togglePassword.setAttribute("aria-pressed", String(visible));
});

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorMessage.hidden = true;
  submitButton.disabled = true;
  submitButton.textContent = "Входим…";
  try {
    const csrf = document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith("explorer_csrf="));
    const headers = { "Content-Type": "application/json" };
    if (csrf) headers["X-Explorer-CSRF"] = decodeURIComponent(csrf.slice("explorer_csrf=".length));
    const response = await fetch("/api/login", {
      method: "POST", credentials: "same-origin", headers,
      body: JSON.stringify({ password: passwordInput.value })
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const detail = typeof body.detail === "string" ? body.detail : body.error;
      throw new Error(response.status === 401 ? "Пароль не подошёл. Проверьте раскладку и попробуйте ещё раз." : (detail || "Не удалось войти в станцию."));
    }
    passwordInput.value = "";
    const requested = new URLSearchParams(window.location.search).get("next");
    const target = requested ? new URL(requested, window.location.origin) : null;
    const safePath = target && target.origin === window.location.origin ? target.pathname + target.search + target.hash : "/";
    window.location.assign(safePath);
  } catch (error) {
    errorMessage.textContent = error.message === "Failed to fetch" ? "Станция недоступна. Проверьте подключение к домашней сети." : error.message;
    errorMessage.hidden = false;
    passwordInput.focus();
  } finally {
    submitButton.disabled = false;
    submitButton.innerHTML = 'Открыть станцию <span aria-hidden="true">→</span>';
  }
});
