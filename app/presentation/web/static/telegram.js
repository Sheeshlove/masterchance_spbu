/*
 * Адаптация сайта под Telegram Mini App.
 *
 * Тот же сайт открывается и в браузере, и внутри Telegram — отдельной сборки
 * нет. Всё, что здесь происходит, применяется ТОЛЬКО внутри Telegram.
 *
 * Важное: SDK Telegram подгружается отсюда динамически и только если мы правда
 * в Telegram. Раньше он стоял тегом <script> в <head> и запрашивался у всех
 * подряд — а telegram.org в России недоступен, и запрос не отклонялся, а
 * висел. Страница при этом рисовалась (стоял defer), но событие load не
 * наступало: у пользователя бесконечно крутился индикатор, а измерялки
 * скорости показывали ноль. Внутри Telegram сам telegram.org, разумеется,
 * доступен всегда.
 */
(function () {
  "use strict";

  var SDK = "https://telegram.org/js/telegram-web-app.js";
  var FLAG = "mc_in_telegram";

  /**
   * Мы внутри Telegram?
   *
   * Открывая Mini App, Telegram дописывает в адрес параметры вида
   * #tgWebAppData=…&tgWebAppPlatform=… . При переходе по ссылке внутри
   * приложения они теряются, поэтому факт запоминается на время сессии.
   */
  function insideTelegram() {
    var url = window.location.href;
    if (url.indexOf("tgWebApp") >= 0) {
      try { sessionStorage.setItem(FLAG, "1"); } catch (e) { /* приватный режим */ }
      return true;
    }
    try {
      return sessionStorage.getItem(FLAG) === "1";
    } catch (e) {
      return false;   // sessionStorage недоступен — считаем, что снаружи
    }
  }

  function applyTelegramLook(tg) {
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

    document.addEventListener("submit", function () {
      try {
        if (tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
      } catch (e) {
        /* haptic есть не везде */
      }
    });
  }

  if (!insideTelegram()) return;   // в браузере к telegram.org не ходим вовсе

  var s = document.createElement("script");
  s.src = SDK;
  s.async = true;
  s.onload = function () {
    var tg = window.Telegram && window.Telegram.WebApp;
    // platform === "unknown" означает, что SDK загрузился вне Telegram
    if (tg && tg.platform && tg.platform !== "unknown") applyTelegramLook(tg);
  };
  document.head.appendChild(s);
})();
