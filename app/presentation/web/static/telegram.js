/*
 * Адаптация сайта под Telegram Mini App.
 *
 * Тот же сайт открывается и в браузере, и внутри Telegram — отдельной сборки
 * нет. Всё, что здесь происходит, применяется ТОЛЬКО внутри Telegram: снаружи
 * скрипт молча выходит и страница остаётся обычной.
 */
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp;
  if (!tg) return;

  // Объект Telegram.WebApp существует и в обычном браузере — там platform
  // равен "unknown". Без этой проверки мы бы прятали шапку на живом сайте.
  var platform = tg.platform || "unknown";
  if (platform === "unknown") return;

  var root = document.documentElement;
  root.classList.add("tg");

  tg.ready();
  if (tg.expand) tg.expand();

  // Красим хром Telegram в цвет бумаги, иначе у страницы видна чужая рамка.
  var paper =
    getComputedStyle(root).getPropertyValue("--paper").trim() || "#FBFAF7";
  try {
    if (tg.setBackgroundColor) tg.setBackgroundColor(paper);
    if (tg.setHeaderColor) tg.setHeaderColor(paper);
  } catch (e) {
    /* старые клиенты принимают только именованные цвета — не беда */
  }

  // Высота вьюпорта в Telegram своя и меняется при раскрытии/клавиатуре.
  function syncHeight() {
    var h = tg.viewportStableHeight || tg.viewportHeight || window.innerHeight;
    root.style.setProperty("--tg-viewport", h + "px");
  }
  syncHeight();
  if (tg.onEvent) tg.onEvent("viewportChanged", syncHeight);

  // Лёгкий отклик на отправку формы — в мини-аппах это ожидаемое поведение.
  document.addEventListener("submit", function () {
    try {
      if (tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
    } catch (e) {
      /* haptic есть не везде */
    }
  });
})();
