INDEX_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Gaia Local Analytics System</title>
  <style>
    :root {
      --sun: #f2c84b;
      --sun-deep: #d99b18;
      --sun-soft: #fff0ad;
      --mint: #e7f5ec;
      --sky: #e9f2ff;
      --clay: #f5ede2;
      --ink: #201d18;
      --line: #d8b64d;
      --paper: #fff8df;
      --panel: #fffdf3;
      --white: #ffffff;
      --green: #2f7d59;
      --red: #9f3a33;
      --amber: #a76500;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        linear-gradient(140deg, rgba(242,200,75,.45) 0, rgba(233,242,255,.72) 38%, rgba(231,245,236,.64) 72%, rgba(255,250,240,1) 100%);
      min-height: 100vh;
    }
    header {
      padding: 22px 28px 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      border-bottom: 1px solid rgba(255,255,255,.5);
      background: rgba(255,253,243,.5);
      backdrop-filter: blur(18px);
    }
    h1 { margin: 0; font-size: 25px; letter-spacing: 0; }
    .subtitle { margin-top: 4px; opacity: .75; font-size: 14px; }
    .header-actions {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      flex-wrap: wrap;
      gap: 8px;
      min-width: 0;
      max-width: 100%;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      min-height: 32px;
      border: 1px solid rgba(32,29,24,.18);
      border-radius: 8px;
      background: rgba(255,255,255,.72);
      backdrop-filter: blur(12px);
      padding: 6px 10px;
      font-size: 12px;
      font-weight: 750;
      color: rgba(32,29,24,.82);
      max-width: 280px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .status-chip {
      gap: 6px;
      min-height: 26px;
      border: 0;
      border-radius: 0;
      background: transparent;
      backdrop-filter: none;
      padding: 3px 2px;
      box-shadow: none;
      cursor: default;
    }
    .status-chip::before {
      content: "";
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--line);
      flex: 0 0 auto;
    }
    .status-chip.ok::before { background: var(--green); }
    .status-chip.warn::before { background: var(--amber); }
    .status-chip.danger::before { background: var(--red); }
    .app-status-bar .status-chip {
      color: rgba(32,29,24,.72);
      font-weight: 650;
      max-width: none;
    }
    main {
      padding: 18px 28px 28px;
    }
    section {
      background: rgba(255,255,255,.56);
      border: 1px solid rgba(255,255,255,.62);
      border-radius: 8px;
      padding: 16px;
      box-shadow: 0 18px 42px rgba(60, 43, 0, .10);
      backdrop-filter: blur(18px);
    }
    .context-panel,
    .dialog-panel,
    .projects-panel,
    .inspector-panel {
      min-width: 0;
    }
    .top-nav {
      display: flex;
      gap: 4px;
      padding: 10px 28px 12px;
      border-bottom: 1px solid rgba(255,255,255,.46);
      background: rgba(255,253,243,.44);
      backdrop-filter: blur(18px);
    }
    .nav-button {
      background: rgba(255,255,255,.54);
      color: var(--ink);
      border-color: rgba(32,29,24,.10);
      box-shadow: none;
    }
    .nav-button.active {
      background: linear-gradient(180deg, rgba(43,42,38,.95), rgba(32,29,24,.92));
      color: white;
      box-shadow: 0 8px 20px rgba(32,29,24,.18);
    }
    .app-status-bar {
      display: none;
      flex-wrap: wrap;
      gap: 8px;
      padding: 0 28px 14px;
      background: rgba(255,253,243,.26);
      border-bottom: 1px solid rgba(255,255,255,.38);
    }
    .screen { display: none; }
    .screen.active { display: block; }
    .dialog-workspace {
      display: grid;
      grid-template-columns: minmax(260px, 340px) minmax(0, 1fr);
      gap: 18px;
      align-items: start;
    }
    .project-dialogue {
      align-items: stretch;
    }
    .dialogue-sidebar,
    .dialogue-main {
      height: calc(100vh - 170px);
      min-height: 0;
    }
    .dialogue-sidebar {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .dialogue-sidebar .conversation-tools {
      display: flex;
      flex-direction: column;
      min-height: 0;
      flex: 1 1 auto;
    }
    .dialogue-main {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .technical-only {
      display: none !important;
    }
    .projects-workspace {
      display: grid;
      grid-template-columns: minmax(280px, 380px) minmax(0, 1fr);
      gap: 18px;
      align-items: start;
    }
    .panel-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }
    .panel-head h2 {
      margin: 0;
      font-size: 17px;
      letter-spacing: 0;
    }
    .panel-head p {
      margin: 4px 0 0;
      color: rgba(32,29,24,.68);
      font-size: 13px;
      line-height: 1.35;
    }
    label { display: block; font-size: 13px; font-weight: 650; margin: 12px 0 6px; }
    select, textarea, input[type=file] {
      width: 100%;
      border: 1px solid rgba(32,29,24,.25);
      border-radius: 6px;
      background: white;
      color: var(--ink);
      padding: 10px;
      font: inherit;
    }
    textarea { min-height: 180px; resize: vertical; line-height: 1.4; }
    button {
      border: 1px solid rgba(32,29,24,.18);
      border-radius: 6px;
      background: linear-gradient(180deg, #2f2d28, #1f1d19);
      color: white;
      padding: 10px 13px;
      font-weight: 700;
      cursor: pointer;
      max-width: 100%;
      white-space: normal;
      box-shadow: 0 8px 18px rgba(32,29,24,.12);
    }
    button.secondary {
      background: rgba(255,255,255,.72);
      color: var(--ink);
      box-shadow: none;
    }
    button.local { background: linear-gradient(180deg, #3a956b, var(--green)); }
    button:disabled { opacity: .55; cursor: not-allowed; }
    .icon-button {
      width: 36px;
      height: 36px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 0;
      font-size: 18px;
      line-height: 1;
    }
    .legacy-inspector-toggle { display: none; }
    .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 14px; }
    .row button { flex: 0 1 auto; }
    .status {
      display: grid;
      grid-template-columns: repeat(5, minmax(96px, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }
    .metric {
      border: 1px solid rgba(255,255,255,.6);
      border-radius: 8px;
      background: rgba(255,255,255,.62);
      padding: 10px;
      min-height: 66px;
      min-width: 0;
    }
    .metric b { display: block; font-size: 12px; opacity: .68; margin-bottom: 6px; }
    .metric span {
      display: block;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 15px;
      font-weight: 750;
    }
    .conversation-shell {
      display: grid;
      grid-template-columns: minmax(180px, 260px) minmax(0, 1fr);
      gap: 12px;
      margin-bottom: 12px;
    }
    .conversation-list {
      display: grid;
      gap: 7px;
      max-height: none;
      overflow: auto;
      min-height: 0;
      flex: 1 1 auto;
    }
    .conversation-row {
      background: white;
      color: var(--ink);
      border: 1px solid rgba(32,29,24,.14);
      text-align: left;
      padding: 9px;
      cursor: pointer;
      transition: border-color .15s ease, background .15s ease, box-shadow .15s ease;
    }
    .conversation-row:hover,
    .conversation-row:focus-visible {
      border-color: rgba(217,155,24,.72);
      background: #fffaf0;
      box-shadow: 0 4px 12px rgba(60,43,0,.10);
      outline: none;
    }
    .conversation-row.active {
      border-color: rgba(217,155,24,.8);
      background: #fff8df;
    }
    .conversation-row b,
    .conversation-row span { display: block; }
    .conversation-row b { font-size: 13px; }
    .conversation-row span { color: rgba(32,29,24,.68); font-size: 12px; margin-top: 3px; }
    .conversation-compose textarea {
      min-height: 72px;
    }
    .dialogue-composer {
      border-top: 1px solid rgba(32,29,24,.10);
      padding-top: 12px;
      background: rgba(255,255,255,.62);
      border-radius: 8px;
    }
    .dialogue-composer label {
      margin-top: 8px;
    }
    .dialogue-actions {
      margin-top: 10px;
    }
    .dialogue-actions button {
      min-height: 42px;
    }
    .dialogue-status {
      display: none;
    }
    .dialogue-summary {
      padding: 10px 12px;
      margin-bottom: 0;
    }
    .dialogue-summary .summary-title {
      margin-bottom: 0;
    }
    .dialogue-summary .summary-grid,
    .dialogue-summary #summaryReason {
      display: none;
    }
    .message-card {
      border: 1px solid rgba(32,29,24,.14);
      border-radius: 8px;
      background: white;
      padding: 10px;
      margin-bottom: 8px;
      font-size: 13px;
      line-height: 1.4;
    }
    .message-card.user { border-left: 4px solid var(--sun-deep); }
    .message-card.assistant { border-left: 4px solid var(--green); }
    .message-card > div {
      white-space: pre-wrap;
    }
    .inbox-toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      margin-bottom: 10px;
    }
    .inbox-grid {
      display: grid;
      grid-template-columns: minmax(220px, 320px) minmax(0, 1fr);
      gap: 12px;
      margin-bottom: 12px;
    }
    .inbox-list {
      display: grid;
      gap: 7px;
      max-height: 360px;
      overflow: auto;
    }
    .inbox-item {
      background: white;
      color: var(--ink);
      border: 1px solid rgba(32,29,24,.14);
      text-align: left;
      padding: 9px;
    }
    .inbox-item.active {
      border-color: rgba(217,155,24,.8);
      background: #fff8df;
    }
    .inbox-item b,
    .inbox-item span { display: block; }
    .inbox-item b { font-size: 13px; }
    .inbox-item span { color: rgba(32,29,24,.68); font-size: 12px; margin-top: 3px; }
    .inbox-preview {
      min-height: 160px;
      max-height: 420px;
      overflow: auto;
      border: 1px solid rgba(32,29,24,.12);
      border-radius: 8px;
      background: white;
      padding: 10px;
      font-size: 13px;
      line-height: 1.42;
      white-space: pre-wrap;
    }
    .scribe-screen {
      display: grid;
      gap: 14px;
    }
    .scribe-scope {
      display: grid;
      grid-template-columns: repeat(3, minmax(160px, 1fr));
      gap: 10px;
    }
    .scope-note {
      border: 1px solid rgba(32,29,24,.12);
      border-radius: 8px;
      background: white;
      padding: 10px;
      font-size: 13px;
      line-height: 1.35;
    }
    .scope-note b {
      display: block;
      margin-bottom: 5px;
    }
    .message-card b { display: block; margin-bottom: 5px; }
    pre {
      margin: 0;
      background: #171612;
      color: #fff8d4;
      border-radius: 8px;
      padding: 14px;
      min-height: 420px;
      white-space: pre-wrap;
      overflow: auto;
      line-height: 1.42;
    }
    .dialog-panel {
      background: rgba(255,253,243,.94);
    }
    .dialog-timeline {
      display: grid;
      gap: 10px;
      margin-top: 12px;
      min-height: 0;
      flex: 1 1 auto;
      overflow: auto;
      padding-right: 2px;
    }
    .dialog-card {
      border: 1px solid rgba(32,29,24,.14);
      border-radius: 8px;
      background: var(--white);
      padding: 12px;
      line-height: 1.42;
      box-shadow: 0 4px 14px rgba(60,43,0,.05);
    }
    .dialog-card.user {
      border-color: rgba(217,155,24,.48);
      background: #fff8df;
    }
    .dialog-card.gaia {
      border-color: rgba(47,125,89,.22);
    }
    .dialog-card.local-answer {
      border-color: rgba(47,125,89,.42);
      background: #f6fff9;
    }
    .dialog-card.error {
      border-color: rgba(159,58,51,.34);
      background: #fff4ef;
    }
    .dialog-card b {
      display: block;
      margin-bottom: 6px;
      font-size: 13px;
    }
    .dialog-card p {
      margin: 0;
      white-space: pre-wrap;
    }
    .dialog-card ul {
      margin: 6px 0 0;
      padding-left: 18px;
    }
    .structured-answer {
      display: grid;
      gap: 10px;
      margin-top: 4px;
    }
    .structured-section {
      border-top: 1px solid rgba(32,29,24,.10);
      padding-top: 8px;
    }
    .structured-section:first-child {
      border-top: 0;
      padding-top: 0;
    }
    .structured-section h4 {
      margin: 0 0 5px;
      font-size: 13px;
    }
    .structured-section p {
      margin: 0;
      white-space: normal;
    }
    .structured-risk-list {
      display: grid;
      gap: 8px;
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .structured-risk {
      border: 1px solid rgba(32,29,24,.12);
      border-radius: 8px;
      background: #fffdf7;
      padding: 9px;
    }
    .structured-risk-title {
      display: flex;
      gap: 8px;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 4px;
      font-weight: 800;
    }
    .risk-level {
      flex: 0 0 auto;
      border-radius: 999px;
      padding: 2px 7px;
      font-size: 11px;
      line-height: 1.2;
      background: #eee8d8;
    }
    .risk-level.high { background: #ffe1d6; color: #8a2418; }
    .risk-level.medium { background: #fff0b8; color: #765200; }
    .risk-level.low { background: #daf3df; color: #1d6b3c; }
    .dialog-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }
    .dialog-actions button {
      padding: 8px 10px;
      font-size: 13px;
    }
    .notes { margin: 10px 0; padding-left: 18px; }
    .notes li { margin: 4px 0; }
    .mask-details {
      display: grid;
      grid-template-columns: repeat(2, minmax(180px, 1fr));
      gap: 8px;
      margin: 10px 0;
    }
    .mask-card {
      background: white;
      border: 1px solid rgba(32,29,24,.14);
      border-radius: 8px;
      padding: 10px;
      min-height: 76px;
    }
    .mask-card b { display: block; font-size: 12px; opacity: .68; margin-bottom: 5px; }
    .mask-card span { display: block; font-size: 13px; line-height: 1.35; }
    .token-note {
      margin: 8px 0 10px;
      padding: 9px 10px;
      border-left: 3px solid var(--green);
      background: rgba(231,245,236,.78);
      border-radius: 6px;
      font-size: 13px;
      line-height: 1.35;
    }
    .veil-table-wrap {
      margin: 10px 0;
      overflow-x: auto;
    }
    .veil-table {
      width: 100%;
      min-width: 760px;
      border-collapse: collapse;
      background: white;
      border: 1px solid rgba(32,29,24,.14);
      border-radius: 8px;
      overflow: hidden;
    }
    .veil-table th,
    .veil-table td {
      border-bottom: 1px solid rgba(32,29,24,.1);
      padding: 9px;
      text-align: left;
      vertical-align: top;
      font-size: 13px;
      line-height: 1.35;
    }
    .veil-table th {
      background: #fff8df;
      font-size: 12px;
      opacity: .78;
    }
    .veil-table tr:last-child td { border-bottom: 0; }
    .summary-panel {
      background: white;
      border: 1px solid rgba(32,29,24,.16);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 12px;
    }
    .summary-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 10px;
    }
    .summary-title b { font-size: 16px; }
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(180px, 1fr));
      gap: 8px;
    }
    .summary-item {
      border: 1px solid rgba(32,29,24,.12);
      border-radius: 8px;
      padding: 9px;
      background: #fffdf7;
      min-height: 64px;
    }
    .summary-item b { display: block; font-size: 12px; opacity: .68; margin-bottom: 5px; }
    .summary-item span { display: block; font-size: 14px; line-height: 1.35; font-weight: 650; }
    .tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin: 10px 0;
      border-bottom: 1px solid rgba(32,29,24,.14);
      padding-bottom: 8px;
    }
    .tab-button {
      background: white;
      color: var(--ink);
      padding: 8px 10px;
      font-weight: 700;
    }
    .tab-button.active {
      background: #2b2a26;
      color: white;
    }
    .tab-panel[hidden] { display: none; }
    .readable-alert {
      border: 1px solid rgba(159,58,51,.34);
      border-radius: 8px;
      background: #fff4ef;
      padding: 10px;
      margin: 10px 0;
      color: var(--red);
      font-weight: 700;
    }
    .empty-state {
      background: white;
      border: 1px solid rgba(32,29,24,.14);
      border-radius: 8px;
      padding: 12px;
      color: rgba(32,29,24,.72);
    }
    .field-hint {
      margin-top: 6px;
      color: rgba(32,29,24,.72);
      font-size: 13px;
      line-height: 1.35;
    }
    .inline-warning {
      border: 1px solid rgba(159,58,51,.28);
      border-radius: 6px;
      background: #fff4ef;
      color: var(--red);
      font-size: 13px;
      font-weight: 700;
      line-height: 1.35;
      margin-top: 10px;
      padding: 9px 10px;
    }
    .inline-warning.neutral {
      border-color: rgba(216,182,77,.55);
      background: #fff8df;
      color: var(--ink);
    }
    .inline-warning[hidden] { display: none; }
    .action-note {
      margin-top: 8px;
      font-size: 13px;
      line-height: 1.35;
    }
    .action-note[hidden] { display: none; }
    .lore-actions {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 12px;
    }
    .lore-list {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }
    .lore-source {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 8px 10px;
      align-items: start;
      background: white;
      border: 1px solid rgba(32,29,24,.14);
      border-radius: 8px;
      padding: 10px;
      font-size: 13px;
      line-height: 1.35;
    }
    .lore-source input { margin-top: 2px; }
    .lore-source b { display: block; font-size: 13px; }
    .lore-source span { display: block; color: rgba(32,29,24,.74); margin-top: 3px; }
    .scribe-plan-body {
      display: block;
      margin: 8px 0 0;
      padding: 10px;
      border: 1px solid rgba(32,29,24,.12);
      border-radius: 8px;
      background: rgba(255,250,240,.76);
      color: var(--ink);
      font: inherit;
      line-height: 1.42;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .local-status {
      margin-top: 10px;
      font-size: 13px;
      line-height: 1.35;
      font-weight: 700;
    }
    .management-panel {
      border: 1px solid rgba(32,29,24,.14);
      border-radius: 8px;
      background: white;
      padding: 12px;
      margin-top: 12px;
    }
    .management-panel:first-child { margin-top: 0; }
    .management-panel h3 {
      margin: 0 0 8px;
      font-size: 14px;
    }
    .mini-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .mini-grid input,
    .mini-grid select {
      min-width: 0;
      width: 100%;
      border: 1px solid rgba(32,29,24,.25);
      border-radius: 6px;
      background: white;
      color: var(--ink);
      padding: 9px;
      font: inherit;
    }
    .management-state {
      margin-top: 8px;
      font-size: 13px;
      line-height: 1.35;
      font-weight: 700;
    }
    .project-list {
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }
    .project-row {
      width: 100%;
      display: block;
      text-align: left;
      background: white;
      color: var(--ink);
      border: 1px solid rgba(32,29,24,.14);
      border-radius: 8px;
      padding: 11px;
    }
    .project-row.active {
      border-color: rgba(217,155,24,.68);
      background: #fff8df;
    }
    .project-row b {
      display: block;
      font-size: 14px;
      margin-bottom: 4px;
    }
    .project-row span {
      display: block;
      font-size: 12px;
      line-height: 1.35;
      color: rgba(32,29,24,.7);
    }
    .project-detail-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(180px, 1fr));
      gap: 8px;
      margin-top: 12px;
    }
    .project-detail-item {
      background: white;
      border: 1px solid rgba(32,29,24,.14);
      border-radius: 8px;
      padding: 10px;
      min-width: 0;
    }
    .project-detail-item b {
      display: block;
      font-size: 12px;
      opacity: .68;
      margin-bottom: 5px;
    }
    .project-detail-item span {
      display: block;
      font-size: 13px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }
    .review-panel {
      background: white;
      border: 1px solid rgba(32,29,24,.16);
      border-radius: 8px;
      padding: 12px;
      margin: 10px 0;
    }
    .review-panel[hidden] { display: none; }
    .review-panel.attention {
      border-color: rgba(217,155,24,.68);
      box-shadow: 0 0 0 3px rgba(242,200,75,.22);
    }
    .review-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 8px;
    }
    .review-head b { font-size: 13px; }
    .review-state { font-size: 12px; font-weight: 750; }
    .review-check {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      margin: 10px 0;
      font-size: 13px;
      line-height: 1.35;
    }
    .review-check input {
      width: 18px;
      height: 18px;
      margin-top: 0;
      flex: 0 0 auto;
    }
    .review-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }
    .prompt-preview {
      width: 100%;
      min-height: 180px;
      max-height: 320px;
      resize: vertical;
      border: 1px solid rgba(32,29,24,.22);
      border-radius: 6px;
      padding: 10px;
      background: #fffdf7;
      color: var(--ink);
      font: 13px ui-monospace, SFMono-Regular, Menlo, monospace;
      line-height: 1.4;
    }
    .inspector-panel {
      max-height: none;
      overflow: visible;
      transition: padding .18s ease;
    }
    .inspector-panel .collapsed-label {
      display: none;
      writing-mode: vertical-rl;
      transform: rotate(180deg);
      font-size: 13px;
      font-weight: 800;
      letter-spacing: 0;
      margin: 12px auto 0;
    }
    body.inspector-collapsed .inspector-panel {
      padding: 12px 10px;
      overflow: hidden;
    }
    body.inspector-collapsed .inspector-body {
      display: none;
    }
    body.inspector-collapsed .inspector-panel .panel-head {
      align-items: center;
      justify-content: center;
      margin-bottom: 0;
    }
    body.inspector-collapsed .inspector-panel .panel-head > div {
      display: none;
    }
    body.inspector-collapsed .inspector-panel .collapsed-label {
      display: block;
    }
    .danger { color: var(--red); font-weight: 700; }
    .ok { color: var(--green); font-weight: 700; }
    .warn { color: var(--amber); font-weight: 700; }
    @media (max-width: 900px) {
      main { padding: 12px; }
      .dialog-workspace,
      .projects-workspace { grid-template-columns: 1fr; }
      .dialogue-sidebar,
      .dialogue-main {
        height: auto;
        min-height: 0;
      }
      .inspector-panel { position: static; max-height: none; }
      body.inspector-collapsed .inspector-panel { min-height: 64px; }
      header { padding: 16px 12px 10px; align-items: flex-start; flex-direction: column; }
      .top-nav { padding: 0 12px 10px; overflow-x: auto; }
      .header-actions { width: 100%; justify-content: flex-start; }
      .chip { max-width: 100%; }
      .row button { flex: 1 1 180px; }
      .status { grid-template-columns: 1fr 1fr; }
      .summary-grid { grid-template-columns: 1fr; }
      .project-detail-grid { grid-template-columns: 1fr; }
      .tabs { overflow-x: auto; flex-wrap: nowrap; }
      .tab-button { flex: 0 0 auto; }
    }
    @media (max-width: 560px) {
      .status { grid-template-columns: 1fr; }
      .row button { flex-basis: 100%; }
      .mask-details { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Gaia Local Analytics System</h1>
      <div class="subtitle">рабочий диалог по проекту с проверкой данных и управляемой памятью</div>
    </div>
    <div class="header-actions">
      <span class="chip status-chip warn" id="workspaceHeaderChip" role="status" title="Работа идет на этом компьютере. Ответ: проверяется. Данные: проверка готова.">Проверяем готовность</span>
    </div>
  </header>
  <nav class="top-nav" aria-label="Основная навигация">
    <button class="nav-button active" id="nav-dialog" type="button" onclick="showScreen('dialog')">Диалог</button>
    <button class="nav-button" id="nav-scribe" type="button" onclick="showScreen('scribe')">Память</button>
    <button class="nav-button" id="nav-projects" type="button" onclick="showScreen('projects')">Проекты</button>
    <button class="nav-button" id="nav-inspector" type="button" onclick="showScreen('inspector')">Проверка</button>
  </nav>
  <div class="app-status-bar" aria-label="Состояние Gaia">
    <span class="chip status-chip" role="status" title="Память проекта используется для ответа">Память проекта</span>
    <span class="chip status-chip" role="status" title="Чувствительные данные проверяются перед маршрутом ответа">Проверка данных</span>
    <span class="chip status-chip" role="status" title="Ответ строится по выбранному контексту">Контекст ответа</span>
    <span class="chip status-chip" role="status" title="Запись в память доступна только после проверки">Запись в память</span>
  </div>
  <main>
    <div id="screen-dialog" class="screen active">
      <div class="dialog-workspace project-dialogue">
        <section class="context-panel dialogue-sidebar">
          <div class="panel-head">
            <div>
              <h2>Рабочее пространство</h2>
              <p>Выбери проект и разговор. Ответы будут строиться по памяти выбранного проекта.</p>
            </div>
          </div>
          <label for="project">Проект</label>
          <select id="project"></select>
          <div id="projectMeta" class="field-hint"></div>

          <label for="profile">Тип задачи</label>
          <select id="profile"></select>
          <div id="profileDescription" class="field-hint"></div>

          <div class="conversation-tools">
            <div class="row">
              <button type="button" onclick="createConversation()">Новый разговор</button>
              <button class="secondary" type="button" onclick="loadConversations()">Обновить</button>
            </div>
            <div id="conversationList" class="conversation-list"></div>
          </div>

          <div id="localStatus" class="local-status">Ответ: готовность не проверялась.</div>
          <div id="scribeState" class="action-note danger" hidden></div>
        </section>
        <section class="dialog-panel dialogue-main">
          <div class="panel-head">
            <div>
              <h2>Проектный диалог</h2>
              <p>Задавай вопросы, прикладывай материалы и сохраняй устойчивые выводы в память после проверки.</p>
            </div>
          </div>
          <div class="status dialogue-status" aria-label="Состояние ответа">
            <div class="metric"><b>Ответ</b><span id="route">-</span></div>
            <div class="metric"><b>Задача</b><span id="profileState">-</span></div>
            <div class="metric"><b>Материалы</b><span id="memory">-</span></div>
            <div class="metric"><b>Данные</b><span id="veil">-</span></div>
            <div class="metric technical-only"><b>Журнал</b><span id="journal">-</span></div>
          </div>
          <div id="processingSummary" class="summary-panel dialogue-summary">
            <div class="summary-title">
              <b>Состояние работы</b>
              <span id="summaryBadge" class="review-state">готов к работе</span>
            </div>
            <div class="summary-grid">
              <div class="summary-item"><b>Состояние</b><span id="summaryState">Готов к работе.</span></div>
              <div class="summary-item"><b>Контекст</b><span id="summaryContext">-</span></div>
              <div class="summary-item"><b>Безопасность</b><span id="summaryExternal">-</span></div>
              <div class="summary-item"><b>Что делать дальше</b><span id="summaryNext">Заполни запрос или приложи файл.</span></div>
            </div>
            <div id="summaryReason" class="readable-alert" hidden></div>
          </div>
          <div id="dialogTimeline" class="dialog-timeline">
            <div class="dialog-card gaia">
              <b>Gaia готова</b>
              <p>Выбери проект, задай вопрос или приложи файл. Подробности проверки останутся в разделе Проверка.</p>
            </div>
          </div>
          <div class="conversation-compose dialogue-composer">
            <label for="query">Сообщение</label>
            <textarea id="query" placeholder="Напиши вопрос или задачу по проекту. Если в тексте есть чувствительные данные, Gaia проверит их перед ответом."></textarea>
            <label for="files">Материалы</label>
            <input id="files" type="file" multiple accept=".txt,.md,.pdf,.docx,.xlsx,.mp3,.mp4,.m4a,.wav,.webm,.mov,.mkv">
            <div id="analysisWarning" class="inline-warning" hidden></div>
            <div class="row dialogue-actions">
              <button class="local" type="button" onclick="sendConversationMessage(true)">Получить ответ</button>
              <button class="secondary" type="button" onclick="analyze()">Подготовить материалы</button>
              <button class="secondary" id="cancelLocalBtn" onclick="cancelLocalAnswer()" disabled>Остановить</button>
              <button class="secondary" id="scribeBtn" onclick="createScribePlan()" disabled>Сохранить выводы в память</button>
              <button class="secondary technical-only" id="localBtn" onclick="localAnswer()" disabled>Ответить по подготовленному запросу</button>
              <button class="secondary technical-only" id="copyBtn" onclick="copyPrompt()" disabled>Скопировать запрос</button>
            </div>
            <div id="conversationState" class="action-note"></div>
          </div>
        </section>
      </div>
    </div>

    <div id="screen-projects" class="screen">
      <div class="projects-workspace">
        <section class="projects-panel">
          <div class="panel-head">
            <div>
              <h2>Проекты</h2>
              <p>Контексты памяти, группы и состояние структуры.</p>
            </div>
          </div>
          <div id="projectList" class="project-list"></div>
        </section>
        <section class="projects-panel">
          <div class="panel-head">
            <div>
              <h2>Управление проектами</h2>
              <p>Создание, привязка к группам, проверка и восстановление структуры.</p>
            </div>
          </div>
          <div class="management-panel">
            <h3>Новая группа</h3>
            <div class="mini-grid">
              <input id="groupCode" placeholder="Код группы">
              <input id="groupTitle" placeholder="Название группы">
            </div>
            <div class="row">
              <button class="secondary" type="button" onclick="createGroupFromForm()">Создать группу</button>
            </div>
          </div>
          <div class="management-panel">
            <h3>Выбранный проект</h3>
            <div class="mini-grid">
              <input id="projectCode" placeholder="Код проекта">
              <input id="projectTitle" placeholder="Название проекта">
              <select id="projectGroup"></select>
              <select id="projectStatus">
                <option value="active">Активный</option>
                <option value="draft">Черновик</option>
                <option value="archived">Архив</option>
              </select>
            </div>
            <div class="row">
              <button class="secondary" type="button" onclick="createProjectFromForm()">Создать проект</button>
              <button class="secondary" type="button" onclick="updateSelectedProject()">Обновить выбранный</button>
              <button class="secondary" id="validateProjectBtn" type="button" onclick="validateSelectedProject()">Проверить</button>
              <button class="secondary" type="button" onclick="repairSelectedProject()">Восстановить структуру</button>
              <button type="button" onclick="openSelectedProjectInDialog()">Открыть в Диалоге</button>
            </div>
            <div id="managementState" class="management-state"></div>
          </div>
          <div class="management-panel">
            <h3>Структура</h3>
            <div id="projectDetails" class="project-detail-grid"></div>
          </div>
        </section>
      </div>
    </div>

    <section id="screen-scribe" class="screen">
      <div class="scribe-screen">
        <div class="panel-head">
          <div>
            <h2>Обновление памяти</h2>
            <p>Новые файлы выбранного проекта превращаются в предложения. Память меняется только после твоего подтверждения.</p>
          </div>
          <button class="secondary" type="button" onclick="showScreen('dialog')">Назад в Диалог</button>
        </div>
        <div class="scribe-scope">
          <div class="scope-note">
            <b>Проект</b>
            <span id="scribeProjectScope">-</span>
          </div>
          <div class="scope-note">
            <b>Что будет прочитано</b>
            <span id="scribeReadScope">Только выбранный файл после кнопки Разобрать выбранный файл.</span>
          </div>
          <div class="scope-note">
            <b>Что пропускается</b>
            <span>Служебная память, скрытые файлы и неподдерживаемые форматы.</span>
          </div>
        </div>
        <div class="inbox-toolbar">
          <button class="secondary" id="scribeInboxRefreshBtn" type="button" onclick="loadScribeInbox()">Обновить список файлов</button>
          <button class="secondary" id="scribeInboxPackageBtn" type="button" onclick="prepareInboxPackage()" disabled>Разобрать выбранный файл</button>
          <button class="secondary" id="scribeInboxIgnoreBtn" type="button" onclick="ignoreInboxItem()" disabled>Скрыть файл из списка</button>
          <span id="scribeInboxState" class="action-note"></span>
        </div>
        <div class="inbox-grid">
          <div id="scribeInboxList" class="inbox-list"></div>
          <div id="scribeInboxPreview" class="inbox-preview">Выбери файл из списка, чтобы увидеть предварительный просмотр. Память не изменится без подтверждения записи.</div>
        </div>
        <div id="scribeDetails" class="mask-details"></div>
        <div id="scribePlanList" class="lore-list"></div>
        <div class="lore-actions">
          <button class="secondary" id="scribePlanBtn" type="button" onclick="createScribePlan()" disabled>Предложить записи в память</button>
          <button id="scribeApplyBtn" type="button" onclick="applyScribePlan()" disabled>Записать выбранное в память</button>
          <button class="secondary" id="scribeDraftBtn" type="button" onclick="createScribeDraft()" disabled>Сохранить черновик без записи</button>
          <span id="scribePlanState" class="action-note"></span>
        </div>
      </div>
    </section>

    <section id="screen-inspector" class="screen inspector-panel">
      <div id="inspectorPanel">
      <div class="panel-head">
        <div>
          <h2>Диагностика</h2>
          <p>Технический слой: собранный запрос, проверка данных, найденная память и состояние обработки.</p>
        </div>
        <button class="secondary icon-button legacy-inspector-toggle" id="inspectorToggle" type="button" onclick="toggleInspector()" aria-expanded="true" title="Свернуть Диагностику">›</button>
        <button class="secondary" type="button" onclick="showScreen('dialog')">Назад в Диалог</button>
      </div>
      <div class="inspector-body">
      <div class="tabs" role="tablist" aria-label="Результат анализа">
        <button class="tab-button active" id="tab-summary" type="button" onclick="showTab('summary')" role="tab">Итог</button>
        <button class="tab-button" id="tab-security" type="button" onclick="showTab('security')" role="tab">Проверка данных</button>
        <button class="tab-button" id="tab-lore" type="button" onclick="showTab('lore')" role="tab">Найденная память</button>
        <button class="tab-button" id="tab-scribe" type="button" onclick="showScreen('scribe')" role="tab">Запись в память</button>
        <button class="tab-button" id="tab-prompt" type="button" onclick="showTab('prompt')" role="tab">Подготовленный запрос</button>
        <button class="tab-button" id="tab-json" type="button" onclick="showTab('json')" role="tab">Технические данные</button>
      </div>
      <div id="panel-summary" class="tab-panel">
        <div id="summaryDetails" class="empty-state">Результат появится после сборки контекста.</div>
      </div>
      <div id="panel-security" class="tab-panel" hidden>
        <ul id="notes" class="notes"></ul>
        <div id="veilDetails" class="mask-details"></div>
      </div>
      <div id="panel-lore" class="tab-panel" hidden>
        <div id="loreDetails" class="mask-details"></div>
        <div id="loreSourceList" class="lore-list"></div>
        <div class="lore-actions">
          <button class="secondary" id="rebuildPromptBtn" type="button" onclick="rebuildPromptWithSelectedLore()" disabled>Пересобрать контекст</button>
          <span id="loreRebuildState" class="action-note"></span>
        </div>
      </div>
      <div id="panel-scribe" class="tab-panel" hidden>
        <div class="empty-state">Обновление памяти вынесено в верхнюю вкладку `Память`, чтобы рабочий процесс не прятался в Диагностике.</div>
        <div class="row">
          <button type="button" onclick="showScreen('scribe')">Открыть обновление памяти</button>
        </div>
      </div>
      <div id="panel-prompt" class="tab-panel" hidden>
        <div id="reviewPanel" class="review-panel" hidden>
          <div class="review-head">
            <b>Проверка перед отправкой</b>
            <span id="reviewState" class="review-state">-</span>
          </div>
          <textarea id="promptPreview" class="prompt-preview" readonly></textarea>
          <label class="review-check" for="reviewConfirm">
            <input id="reviewConfirm" type="checkbox" onchange="updateCopyState()">
            <span>Контекст проверен, исходные персональные данные не видны, внешний анализ разрешаю.</span>
          </label>
          <div class="review-actions">
            <button class="secondary" type="button" onclick="showTab('prompt')">Показать запрос</button>
            <button class="secondary" id="reviewCopyBtn" type="button" onclick="copyPrompt()" disabled>Скопировать запрос</button>
          </div>
        </div>
      </div>
      <div id="panel-json" class="tab-panel" hidden>
        <pre id="output">Технические данные появятся после запуска.</pre>
      </div>
      </div>
      </div>
    </section>
  </main>
  <script>
    let lastPrompt = "";
    let lastPackage = null;
    let currentJobId = "";
    let pollTimer = null;
    let profilesById = {};
    let projectRecords = [];
    let groupsByCode = {};
    let conversations = [];
    let currentConversation = null;
    let scribeInboxItems = [];
    let currentScribeInboxItem = null;
    let loreAvailableSources = [];
    let currentScribePlan = null;
    let localAnswerController = null;
    let localAnswerTimeout = null;
    let localAnswerCanceled = false;
    let localAnswerTimedOut = false;
    const LOCAL_ANSWER_TIMEOUT_MS = 195000;

    function showScreen(name) {
      for (const screen of document.querySelectorAll('.screen')) {
        screen.classList.toggle('active', screen.id === `screen-${name}`);
      }
      for (const button of document.querySelectorAll('.nav-button')) {
        button.classList.toggle('active', button.id === `nav-${name}`);
      }
      if (name === 'projects') {
        renderProjectList();
        renderProjectDetails();
      }
      if (name === 'dialog') {
        loadConversations();
      }
      if (name === 'scribe') {
        loadScribeInbox();
      }
    }

    function toggleInspector() {
      const collapsed = document.body.classList.toggle('inspector-collapsed');
      const button = document.getElementById('inspectorToggle');
      if (!button) return;
      button.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      button.title = collapsed ? 'Развернуть Диагностику' : 'Свернуть Диагностику';
      button.textContent = collapsed ? '‹' : '›';
    }

    function resetDialogTimeline() {
      const root = document.getElementById('dialogTimeline');
      root.innerHTML = "";
    }

    function addDialogCard(kind, title, content, actions) {
      const root = document.getElementById('dialogTimeline');
      const card = document.createElement('div');
      card.className = `dialog-card ${kind || 'gaia'}`.trim();
      const head = document.createElement('b');
      head.textContent = title;
      card.appendChild(head);
      if (Array.isArray(content)) {
        const list = document.createElement('ul');
        for (const line of content) {
          const item = document.createElement('li');
          item.textContent = line;
          list.appendChild(item);
        }
        card.appendChild(list);
      } else if (isStructuredAnswer(content)) {
        renderStructuredAnswer(card, content);
      } else {
        const paragraph = document.createElement('p');
        paragraph.textContent = content || "";
        card.appendChild(paragraph);
      }
      if (actions && actions.length) {
        const row = document.createElement('div');
        row.className = 'dialog-actions';
        for (const action of actions) {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = action.className || 'secondary';
          button.textContent = action.label;
          button.onclick = action.onClick;
          row.appendChild(button);
        }
        card.appendChild(row);
      }
      root.appendChild(card);
      root.scrollTop = root.scrollHeight;
      return card;
    }

    function isStructuredAnswer(value) {
      return !!value && typeof value === 'object' && !Array.isArray(value) && (
        value.summary ||
        (value.key_observations || []).length ||
        (value.risks || []).length ||
        (value.open_questions || []).length ||
        (value.next_steps || []).length
      );
    }

    function renderStructuredAnswer(parent, answer) {
      const root = document.createElement('div');
      root.className = 'structured-answer';
      if (answer.summary) {
        addStructuredTextSection(root, 'Краткий вывод', answer.summary);
      }
      addStructuredListSection(root, 'Ключевые наблюдения', answer.key_observations);
      addStructuredRisksSection(root, answer.risks);
      addStructuredListSection(root, 'Открытые вопросы', answer.open_questions);
      addStructuredListSection(root, 'Следующие шаги', answer.next_steps);
      parent.appendChild(root);
    }

    function addStructuredTextSection(root, title, text) {
      const section = structuredSection(title);
      const paragraph = document.createElement('p');
      paragraph.textContent = text || "";
      section.appendChild(paragraph);
      root.appendChild(section);
    }

    function addStructuredListSection(root, title, items) {
      if (!items || !items.length) return;
      const section = structuredSection(title);
      const list = document.createElement('ul');
      for (const line of items) {
        const item = document.createElement('li');
        item.textContent = line;
        list.appendChild(item);
      }
      section.appendChild(list);
      root.appendChild(section);
    }

    function addStructuredRisksSection(root, risks) {
      if (!risks || !risks.length) return;
      const section = structuredSection('Риски');
      const list = document.createElement('ul');
      list.className = 'structured-risk-list';
      for (const risk of risks) {
        const item = document.createElement('li');
        item.className = 'structured-risk';
        const titleRow = document.createElement('div');
        titleRow.className = 'structured-risk-title';
        const title = document.createElement('span');
        title.textContent = risk.title || 'Риск';
        const level = document.createElement('span');
        level.className = `risk-level ${risk.level || 'medium'}`;
        level.textContent = riskLevelLabel(risk.level);
        titleRow.appendChild(title);
        titleRow.appendChild(level);
        item.appendChild(titleRow);
        if (risk.reason) {
          const reason = document.createElement('p');
          reason.textContent = risk.reason;
          item.appendChild(reason);
        }
        if (risk.mitigation) {
          const mitigation = document.createElement('p');
          mitigation.textContent = `Следующий шаг: ${risk.mitigation}`;
          item.appendChild(mitigation);
        }
        list.appendChild(item);
      }
      section.appendChild(list);
      root.appendChild(section);
    }

    function structuredSection(title) {
      const section = document.createElement('section');
      section.className = 'structured-section';
      const heading = document.createElement('h4');
      heading.textContent = title;
      section.appendChild(heading);
      return section;
    }

    function riskLevelLabel(level) {
      if (level === 'high') return 'высокий';
      if (level === 'low') return 'низкий';
      return 'средний';
    }

    function showInspectorTab(name) {
      showScreen('inspector');
      showTab(name);
    }

    function currentUserRequestLabel() {
      const query = document.getElementById('query').value.trim();
      const files = Array.from(document.getElementById('files').files).map((file) => file.name);
      if (query) return query;
      if (files.length) return `Файлы: ${files.join(', ')}`;
      return 'Запрос без текста';
    }

    function loreCoverageLabel(data) {
      const selected = (data.memory_sources || []).length;
      const total = data.memory_total_sections || 0;
      if (!selected) {
        return `Память проекта: подтвержденный контекст по запросу не найден (${selected} из ${total} разделов).`;
      }
      return `Память проекта: выбрано ${selected} из ${total} разделов.`;
    }

    function loreCoverageNextStep(data) {
      if ((data.memory_sources || []).length) {
        return 'Выбранные разделы доступны во вкладке найденной памяти.';
      }
      return 'Уточни запрос терминами проекта или открой проверку, чтобы вручную выбрать подходящие разделы.';
    }

    function externalRouteReadyText() {
      return 'Внешний маршрут доступен, но не активирован.';
    }

    function safeTokenExplanation(data) {
      const examples = safeTokenExamples(data).join(', ');
      return `Защитные метки вида ${examples} заменяют скрытые персональные данные. Исходные значения остаются на этом компьютере и не показываются в очищенных материалах.`;
    }

    function safeTokenExamples(data) {
      const tokens = [];
      const reviews = [];
      if (data?.query_mask_review) reviews.push(data.query_mask_review);
      if (data?.prompt_mask_review) reviews.push(data.prompt_mask_review);
      for (const file of data?.files || []) {
        if (file.mask_review) reviews.push(file.mask_review);
      }
      for (const review of reviews) {
        for (const finding of review.findings || []) {
          if (finding.token && !tokens.includes(finding.token)) tokens.push(finding.token);
          if (tokens.length >= 3) return tokens;
        }
      }
      return ['[PERSON_1]', '[PHONE_1]', '[INN_1]'];
    }

    function packageDialogLines(data) {
      return [
        `Проект: ${data.project || '-'}`,
        `Группа: ${data.group_title || 'без группы'}`,
        `Профиль: ${data.profile_title || data.profile_id || '-'}`,
        loreCoverageLabel(data),
        `Проверка данных: ${data.query_mask_status || '-'}, замен ${data.query_mask_replacements || 0}`,
        data.local_fallback_required ? 'Внешний маршрут: заблокирован' : externalRouteReadyText()
      ];
    }

    function errorMessage(error, fallback) {
      if (!error) return fallback;
      if (typeof error === 'string') return error;
      if (error.message) return error.message;
      if (error.code) return error.code;
      return fallback;
    }

    const headerReadiness = {
      answer: 'checking',
      data: 'ready'
    };

    function refreshWorkspaceHeader() {
      const chip = document.getElementById('workspaceHeaderChip');
      if (!chip) return;
      const answerText = {
        checking: 'Ответ: проверяется',
        available: 'Ответ: доступен',
        busy: 'Ответ: занят',
        unavailable: 'Ответ: недоступен'
      }[headerReadiness.answer] || 'Ответ: проверяется';
      const dataText = {
        ready: 'Данные: проверка готова',
        verified: 'Данные: проверены',
        localOnly: 'Данные: только локально',
        needsReview: 'Данные: нужна проверка'
      }[headerReadiness.data] || 'Данные: проверка готова';
      chip.title = `Работа идет на этом компьютере. ${answerText}. ${dataText}.`;
      if (headerReadiness.answer === 'unavailable' || headerReadiness.data === 'needsReview') {
        chip.textContent = 'Нужна проверка';
        chip.className = 'chip status-chip danger';
      } else if (
        headerReadiness.answer === 'checking' ||
        headerReadiness.answer === 'busy' ||
        headerReadiness.data === 'localOnly'
      ) {
        chip.textContent = 'Проверяем готовность';
        chip.className = 'chip status-chip warn';
      } else {
        chip.textContent = 'Готово к работе';
        chip.className = 'chip status-chip ok';
      }
    }

    function setHeaderLmState(data) {
      if (data.available) {
        headerReadiness.answer = 'available';
      } else if (data.status === 'timeout') {
        headerReadiness.answer = 'busy';
      } else {
        headerReadiness.answer = 'unavailable';
      }
      refreshWorkspaceHeader();
    }

    function setHeaderVeilState(data) {
      if (!data) return;
      if (hasUnresolvedPii(data)) {
        headerReadiness.data = 'needsReview';
      } else {
        headerReadiness.data = data.local_fallback_required ? 'localOnly' : 'verified';
      }
      refreshWorkspaceHeader();
    }

    async function loadProjects() {
      const res = await fetch('/api/projects');
      const data = await res.json();
      const select = document.getElementById('project');
      select.innerHTML = "";
      projectRecords = data.project_records || (data.projects || []).map((name) => ({ name, title: name, code: '', group_code: '' }));
      groupsByCode = {};
      for (const group of data.groups || []) groupsByCode[group.code] = group;
      renderGroupSelect();
      for (const project of projectRecords) {
        const option = document.createElement('option');
        option.value = project.name;
        option.textContent = project.group_title ? `${project.group_title} / ${project.title}` : project.title || project.name;
        select.appendChild(option);
      }
      select.onchange = () => {
        syncProjectState();
        loadConversations();
        loadScribeInbox();
        invalidatePreparedPackage('Пространство памяти изменено. Пересобери контекст перед локальным ответом.');
      };
      syncProjectManagementForm();
      renderProjectList();
      renderProjectDetails();
      loadConversations();
      loadScribeInbox();
    }

    async function loadConversations() {
      const project = document.getElementById('project')?.value || "";
      const root = document.getElementById('conversationList');
      if (!root || !project) return;
      root.innerHTML = "";
      const res = await fetch(`/api/conversations?project=${encodeURIComponent(project)}`);
      const data = await res.json();
      if (!res.ok) {
        root.appendChild(emptyCard('Разговоры', errorMessage(data.error, 'Не удалось загрузить разговоры.')));
        return;
      }
      conversations = data.conversations || [];
      if (!currentConversation || currentConversation.project !== project) {
        currentConversation = conversations[0] || null;
      } else {
        currentConversation = conversations.find((item) => item.id === currentConversation.id) || conversations[0] || null;
      }
      renderConversationList();
      renderCurrentConversation();
    }

    function renderConversationList() {
      const root = document.getElementById('conversationList');
      if (!root) return;
      root.innerHTML = "";
      if (!conversations.length) {
        root.appendChild(emptyCard('Разговоры', 'Истории для проекта пока нет.'));
        return;
      }
      for (const conversation of conversations) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `conversation-row ${currentConversation?.id === conversation.id ? 'active' : ''}`.trim();
        const title = document.createElement('b');
        title.textContent = conversation.title || 'Разговор';
        const meta = document.createElement('span');
        meta.textContent = `${(conversation.messages || []).length} сообщений; ${conversation.updated_at || '-'}`;
        button.appendChild(title);
        button.appendChild(meta);
        button.onclick = () => {
          currentConversation = conversation;
          renderConversationList();
          renderCurrentConversation();
        };
        root.appendChild(button);
      }
    }

    function renderCurrentConversation() {
      resetDialogTimeline();
      if (!currentConversation) {
        addDialogCard('gaia', 'Разговор проекта', 'Создай новый разговор или подготовь материалы для ответа.');
        return;
      }
      const messages = currentConversation.messages || [];
      if (!messages.length) {
        addDialogCard('gaia', currentConversation.title || 'Новый разговор', 'История пуста. Напиши первое сообщение ниже.');
        return;
      }
      for (const message of messages) {
        addMessageCard(message);
      }
    }

    function addMessageCard(message) {
      const root = document.getElementById('dialogTimeline');
      const card = document.createElement('div');
      card.className = `message-card ${message.role || 'user'}`;
      const head = document.createElement('b');
      head.textContent = `${message.role || 'message'} · ${message.created_at || ''}`;
      const body = document.createElement('div');
      if (message.role === 'assistant' && isStructuredAnswer(message.structured_answer)) {
        renderStructuredAnswer(body, message.structured_answer);
      } else {
        body.textContent = message.masked_text || message.text || "";
      }
      card.appendChild(head);
      card.appendChild(body);
      root.appendChild(card);
      root.scrollTop = root.scrollHeight;
    }

    async function createConversation() {
      const project = document.getElementById('project').value;
      const res = await fetch('/api/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project })
      });
      const data = await res.json();
      if (!res.ok) {
        conversationMessage(errorMessage(data.error, 'Не удалось создать диалог.'), false);
        return;
      }
      currentConversation = data;
      await loadConversations();
        conversationMessage('Новый разговор создан.', true);
    }

    async function sendConversationMessage(runLocal) {
      if (!currentConversation) {
        conversationMessage('Разговор не выбран. Gaia создаёт новый разговор для этого сообщения...', true);
        await createConversation();
      }
      if (!currentConversation) {
        conversationMessage('Не удалось создать разговор. Выбери разговор из списка слева или проверь проект.', false);
        return;
      }
      const input = document.getElementById('query');
      const filesInput = document.getElementById('files');
      const text = input.value.trim();
      if (!text && !filesInput.files.length) {
        conversationMessage('Добавь сообщение или материал.', false);
        return;
      }
      conversationMessage(runLocal ? 'Gaia проверяет данные и готовит ответ...' : 'Gaia проверяет данные и готовит материалы...', true);
      const form = new FormData();
      form.append('text', text);
      form.append('profile', document.getElementById('profile').value);
      form.append('run_local', runLocal ? 'true' : 'false');
      for (const file of filesInput.files) form.append('files', file);
      const res = await fetch(`/api/conversations/${currentConversation.id}/messages`, {
        method: 'POST',
        body: form
      });
      const data = await res.json();
      if (!res.ok) {
        conversationMessage(errorMessage(data.error, 'Не удалось отправить сообщение.'), false);
        setOutput(JSON.stringify(data, null, 2));
        return;
      }
      input.value = "";
      filesInput.value = "";
      currentConversation = data.conversation;
      lastPackage = data.package;
      lastPrompt = data.package?.prompt || "";
      currentJobId = data.package?.run_id || "";
      renderPackage(data.package);
      currentConversation = data.conversation;
      renderCurrentConversation();
      await loadConversations();
      if (runLocal) renderConversationLocalSummary(data);
      if (runLocal && data.local_result && !data.local_result.ok) {
        conversationMessage(errorMessage(data.local_result.error, 'Сообщение сохранено, но ответ не получен.'), false);
        addDialogCard('error', 'Ответ не получен', errorMessage(data.local_result.error, 'Локальный ответ не вернулся.'));
      } else if (runLocal && data.local_result && data.local_result.prompt_compacted) {
        conversationMessage('Ответ получен. Материалы были сокращены под окно локальной модели.', true);
        addDialogCard('gaia', 'Защита данных', safeTokenExplanation(data.package));
      } else {
        conversationMessage('Сообщение сохранено в истории проекта.', true);
        if (runLocal && data.local_result) addDialogCard('gaia', 'Защита данных', safeTokenExplanation(data.package));
      }
      setOutput(JSON.stringify(redactTechnicalPayload(data), null, 2));
    }

    async function archiveCurrentConversation() {
      if (!currentConversation) return;
      const res = await fetch(`/api/conversations/${currentConversation.id}/archive`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) {
        conversationMessage(errorMessage(data.error, 'Не удалось архивировать диалог.'), false);
        return;
      }
      currentConversation = null;
      await loadConversations();
      conversationMessage('Разговор архивирован.', true);
    }

    function conversationMessage(message, ok) {
      const state = document.getElementById('conversationState');
      if (!state) return;
      state.textContent = message;
      state.className = ok ? 'action-note ok' : 'action-note danger';
    }

    function renderGroupSelect() {
      const select = document.getElementById('projectGroup');
      if (!select) return;
      select.innerHTML = "";
      const empty = document.createElement('option');
      empty.value = "";
      empty.textContent = "Без группы";
      select.appendChild(empty);
      for (const group of Object.values(groupsByCode).sort((a, b) => (a.title || '').localeCompare(b.title || ''))) {
        const option = document.createElement('option');
        option.value = group.code;
        option.textContent = `${group.code} - ${group.title}`;
        select.appendChild(option);
      }
    }

    function selectedProjectRecord() {
      const name = document.getElementById('project').value;
      return projectRecords.find((project) => project.name === name) || null;
    }

    function syncProjectManagementForm() {
      const project = selectedProjectRecord();
      const meta = document.getElementById('projectMeta');
      if (!project) {
        meta.textContent = 'Проекты не найдены.';
        renderProjectList();
        renderProjectDetails();
        return;
      }
      document.getElementById('projectCode').value = project.code || "";
      document.getElementById('projectTitle').value = project.title || project.name || "";
      document.getElementById('projectGroup').value = project.group_code || "";
      document.getElementById('projectStatus').value = project.status || "active";
      const group = project.group_title ? `; группа: ${project.group_title}` : '; без группы';
      meta.textContent = `Код: ${project.code || '-'}${group}; состояние: ${project.health || 'ok'}`;
    }

    function syncProjectState() {
      syncProjectManagementForm();
      renderProjectList();
      renderProjectDetails();
    }

    function renderProjectList() {
      const root = document.getElementById('projectList');
      if (!root) return;
      root.innerHTML = "";
      if (!projectRecords.length) {
        root.appendChild(emptyCard('Проекты', 'Проекты не найдены.'));
        return;
      }
      const selected = selectedProjectRecord();
      for (const project of projectRecords) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `project-row ${selected?.name === project.name ? 'active' : ''}`.trim();
        const title = document.createElement('b');
        title.textContent = project.title || project.name;
        const meta = document.createElement('span');
        const group = project.group_title ? `${project.group_title}; ` : 'без группы; ';
        meta.textContent = `${group}код: ${project.code || '-'}; статус: ${project.status || 'active'}; health: ${project.health || 'ok'}`;
        button.appendChild(title);
        button.appendChild(meta);
        button.onclick = () => {
          document.getElementById('project').value = project.name;
          syncProjectState();
        };
        root.appendChild(button);
      }
    }

    function renderProjectDetails() {
      const root = document.getElementById('projectDetails');
      if (!root) return;
      root.innerHTML = "";
      const project = selectedProjectRecord();
      if (!project) {
        root.appendChild(emptyCard('Структура', 'Выбери проект из списка.'));
        return;
      }
      const details = [
        ['Название', project.title || project.name || '-'],
        ['Код', project.code || '-'],
        ['Группа', project.group_title || 'без группы'],
        ['Статус', project.status || 'active'],
        ['Health', project.health || 'ok'],
        ['Папка', project.path || '-'],
        ['Память', project.memory_path || '-'],
        ['Источники', project.sources_path || '-'],
        ['Журнал', project.journal_path || '-'],
        ['Graph index', project.graph_index_path || '-']
      ];
      for (const [label, value] of details) {
        const item = document.createElement('div');
        item.className = 'project-detail-item';
        const title = document.createElement('b');
        title.textContent = label;
        const text = document.createElement('span');
        text.textContent = value;
        item.appendChild(title);
        item.appendChild(text);
        root.appendChild(item);
      }
      for (const issue of project.issues || []) {
        const item = document.createElement('div');
        item.className = 'project-detail-item';
        const title = document.createElement('b');
        title.textContent = 'Проблема';
        const text = document.createElement('span');
        text.className = 'danger';
        text.textContent = issue;
        item.appendChild(title);
        item.appendChild(text);
        root.appendChild(item);
      }
    }

    function openSelectedProjectInDialog() {
      syncProjectState();
      showScreen('dialog');
      document.getElementById('query').focus();
    }

    function managementMessage(message, ok, pending) {
      const state = document.getElementById('managementState');
      state.textContent = message;
      state.className = pending ? 'management-state warn' : (ok ? 'management-state ok' : 'management-state danger');
    }

    async function createGroupFromForm() {
      const payload = {
        code: document.getElementById('groupCode').value.trim(),
        title: document.getElementById('groupTitle').value.trim()
      };
      const res = await fetch('/api/groups', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) {
        managementMessage(errorMessage(data.error, 'Не удалось создать группу.'), false);
        return;
      }
      managementMessage(`Группа создана: ${data.title}`, true);
      await loadProjects();
    }

    async function createProjectFromForm() {
      const payload = projectFormPayload();
      const res = await fetch('/api/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) {
        managementMessage(errorMessage(data.error, 'Не удалось создать проект.'), false);
        return;
      }
      managementMessage(`Проект создан: ${data.title}`, true);
      await loadProjects();
      document.getElementById('project').value = data.name;
      syncProjectState();
    }

    async function updateSelectedProject() {
      const current = selectedProjectRecord();
      if (!current) return managementMessage('Выбери проект.', false);
      const payload = projectFormPayload();
      const res = await fetch(`/api/projects/${encodeURIComponent(current.name)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) {
        managementMessage(errorMessage(data.error, 'Не удалось обновить проект.'), false);
        return;
      }
      managementMessage(`Проект обновлен: ${data.title}`, true);
      await loadProjects();
      document.getElementById('project').value = data.name;
      syncProjectState();
    }

    async function validateSelectedProject() {
      const current = selectedProjectRecord();
      if (!current) return managementMessage('Выбери проект.', false);
      const button = document.getElementById('validateProjectBtn');
      managementMessage(`Проверяю структуру проекта: ${current.title || current.name}...`, true, true);
      if (button) button.disabled = true;
      try {
        const res = await fetch(`/api/projects/${encodeURIComponent(current.name)}/validate`, { method: 'POST' });
        const data = await res.json();
        if (!res.ok) {
          managementMessage(errorMessage(data.error, 'Не удалось проверить проект.'), false);
          return;
        }
        managementMessage(data.ok ? 'Структура проекта в порядке.' : `Найдены проблемы: ${(data.issues || []).join('; ')}`, data.ok);
      } catch (error) {
        managementMessage(`Не удалось проверить проект: ${error.message || error}`, false);
      } finally {
        if (button) button.disabled = false;
      }
    }

    async function repairSelectedProject() {
      const current = selectedProjectRecord();
      if (!current) return managementMessage('Выбери проект.', false);
      const res = await fetch(`/api/projects/${encodeURIComponent(current.name)}/repair`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) {
        managementMessage(errorMessage(data.error, 'Не удалось починить проект.'), false);
        return;
      }
      managementMessage(`Структура восстановлена: ${data.title}`, true);
      await loadProjects();
    }

    function projectFormPayload() {
      return {
        code: document.getElementById('projectCode').value.trim(),
        title: document.getElementById('projectTitle').value.trim(),
        group_code: document.getElementById('projectGroup').value,
        status: document.getElementById('projectStatus').value,
        context_inheritance: true
      };
    }

    async function loadProfiles() {
      const res = await fetch('/api/profiles');
      const data = await res.json();
      const select = document.getElementById('profile');
      select.innerHTML = "";
      profilesById = {};
      for (const profile of data.profiles) {
        profilesById[profile.id] = profile;
        const option = document.createElement('option');
        option.value = profile.id;
        option.textContent = profile.title;
        option.title = profile.description;
        select.appendChild(option);
      }
      select.onchange = () => {
        updateProfileDescription();
        invalidatePreparedPackage('Профиль задачи изменен. Пересобери контекст перед локальным ответом.');
      };
      updateProfileDescription();
    }

    function invalidatePreparedPackage(message) {
      if (!lastPackage && !lastPrompt && !currentJobId) return;
      lastPackage = null;
      lastPrompt = "";
      currentJobId = "";
      loreAvailableSources = [];
      document.getElementById('copyBtn').disabled = true;
      document.getElementById('localBtn').disabled = true;
      document.getElementById('scribeBtn').disabled = true;
      document.getElementById('rebuildPromptBtn').disabled = true;
      document.getElementById('reviewCopyBtn').disabled = true;
      resetReviewPanel();
      showAnalysisWarning(message || 'Форма изменена. Пересобери контекст перед следующим действием.', true);
    }

    function bindFormDirtyHandlers() {
      const queryInput = document.getElementById('query');
      const filesInput = document.getElementById('files');
      queryInput.addEventListener('input', () => invalidatePreparedPackage('Запрос изменен. Пересобери контекст перед локальным ответом.'));
      filesInput.addEventListener('change', () => invalidatePreparedPackage('Список файлов изменен. Пересобери контекст перед локальным ответом.'));
    }

    async function analyze() {
      const queryInput = document.getElementById('query');
      const filesInput = document.getElementById('files');
      const query = queryInput.value.trim();
      const hasFiles = filesInput.files.length > 0;
      if (!query && !hasFiles) {
        showAnalysisWarning('Добавь запрос или файл для анализа.');
        showTab('summary');
        setProcessingSummary({
          badge: 'нужно действие',
          state: 'Запуск остановлен.',
          context: currentContextLabel(),
          external: '-',
          next: 'Добавь запрос или файл для анализа.',
          reason: 'Добавь запрос или файл для анализа.'
        });
        setSummaryDetails(['Пустая форма не отправлена: задача обработки не создана.']);
        addDialogCard('error', 'Запуск остановлен', 'Добавь запрос или файл для анализа.');
        return;
      }
      if (!query && hasFiles) {
        showAnalysisWarning('Запрос не указан: анализ будет строиться по файлу и профилю.', true);
      } else {
        clearAnalysisWarning();
      }
      const form = new FormData();
      form.append('project', document.getElementById('project').value);
      form.append('profile', document.getElementById('profile').value);
      form.append('query', queryInput.value);
      for (const file of filesInput.files) form.append('files', file);
      showTab('summary');
      resetDialogTimeline();
      addDialogCard('user', 'Запрос пользователя', currentUserRequestLabel());
      addDialogCard('gaia', 'Gaia собирает контекст', [
        `Проект и профиль: ${currentContextLabel()}`,
        hasFiles ? `Файлы: ${Array.from(filesInput.files).map((file) => file.name).join(', ')}` : 'Файлы: не приложены',
        'Технические данные доступны в Диагностике.'
      ], [
        { label: 'Открыть Job', onClick: () => showInspectorTab('json') }
      ]);
      setProcessingSummary({
        badge: 'обработка',
        state: 'Gaia создает задачу.',
        context: currentContextLabel(),
        external: '-',
        next: 'Дождись результата обработки.'
      });
      setSummaryDetails(['Gaia собирает контекст. Интерфейс остается доступным, результат появится здесь после завершения обработки.']);
      setOutput('Gaia собирает пакет...');
      const res = await fetch('/api/analyze', { method: 'POST', body: form });
      const data = await res.json();
      if (!res.ok) {
        renderReadableError(errorMessage(data.error, 'Не удалось создать задачу анализа.'), data);
        return;
      }
      currentJobId = data.job_id;
      document.getElementById('route').textContent = `обработка: ${data.status}`;
      document.getElementById('profileState').textContent = document.getElementById('profile').selectedOptions[0]?.textContent || '-';
      document.getElementById('memory').textContent = '-';
      document.getElementById('veil').textContent = '-';
      document.getElementById('journal').textContent = '-';
      document.getElementById('copyBtn').disabled = true;
      document.getElementById('localBtn').disabled = true;
      document.getElementById('cancelLocalBtn').disabled = true;
      document.getElementById('scribeBtn').disabled = true;
      document.getElementById('reviewCopyBtn').disabled = true;
      document.getElementById('notes').innerHTML = "";
      document.getElementById('veilDetails').innerHTML = "";
      document.getElementById('loreDetails').innerHTML = "";
      document.getElementById('loreSourceList').innerHTML = "";
      document.getElementById('rebuildPromptBtn').disabled = true;
      document.getElementById('loreRebuildState').textContent = "";
      resetReviewPanel();
      setProcessingSummary({
        badge: `обработка: ${data.status}`,
        state: 'Задача создана.',
        context: currentContextLabel(),
        external: 'Ожидает проверки данных.',
        next: 'Дождись завершения обработки.'
      });
      setOutput(JSON.stringify(data, null, 2));
      pollJob(currentJobId);
    }

    function updateProfileDescription() {
      const select = document.getElementById('profile');
      const profile = profilesById[select.value] || {};
      document.getElementById('profileDescription').textContent = profile.description || '';
    }

    function showAnalysisWarning(message, neutral) {
      const warning = document.getElementById('analysisWarning');
      warning.textContent = message;
      warning.className = neutral ? 'inline-warning neutral' : 'inline-warning';
      warning.hidden = false;
    }

    function clearAnalysisWarning() {
      const warning = document.getElementById('analysisWarning');
      warning.textContent = "";
      warning.hidden = true;
      warning.className = 'inline-warning';
    }

    async function pollJob(jobId) {
      if (pollTimer) window.clearTimeout(pollTimer);
      const res = await fetch(`/api/jobs/${jobId}`);
      const job = await res.json();
      if (!res.ok) {
        renderReadableError(errorMessage(job.error, 'Не удалось получить статус обработки.'), job);
        return;
      }
      document.getElementById('route').textContent = `${job.status} ${job.progress}%`;
      setProcessingSummary({
        badge: `${job.status} ${job.progress}%`,
        state: 'Gaia обрабатывает задачу.',
        context: currentContextLabel(),
        external: 'Еще не определен.',
        next: 'Дождись результата обработки.'
      });
      if (job.status === 'done') {
        renderPackage(job.result);
        return;
      }
      if (job.status === 'failed') {
        document.getElementById('route').textContent = 'failed';
        renderReadableError(errorMessage(job.error, job.message || 'Задача завершилась ошибкой.'), job);
        return;
      }
      setOutput(JSON.stringify(job, null, 2));
      pollTimer = window.setTimeout(() => pollJob(jobId), 900);
    }

    function renderPackage(data) {
      lastPackage = data;
      loreAvailableSources = data.memory_sources || [];
      lastPrompt = data.prompt || "";
      showTab('summary');
      const route = document.getElementById('route');
      route.textContent = data.route;
      route.title = data.route || "";
      document.getElementById('profileState').textContent = data.profile_title || data.profile_id || '-';
      document.getElementById('memory').textContent = `${data.memory_chars} зн.`;
      document.getElementById('veil').textContent = `${data.query_mask_status}, замен ${data.query_mask_replacements}`;
      document.getElementById('journal').textContent = data.journal_path ? 'записан' : '-';
      document.getElementById('copyBtn').disabled = true;
      document.getElementById('localBtn').disabled = false;
      updateScribeState(data);
      const notes = document.getElementById('notes');
      notes.innerHTML = "";
      for (const note of data.policy_notes) {
        const li = document.createElement('li');
        li.textContent = note;
        li.className = data.local_fallback_required ? 'danger' : 'ok';
        notes.appendChild(li);
      }
      renderMaskDetails(data);
      renderMemorySources(data);
      currentScribePlan = null;
      renderScribePlan(null);
      renderReviewPanel(data);
      renderPackageSummary(data);
      setHeaderVeilState(data);
      addDialogCard(data.local_fallback_required ? 'error' : 'gaia', 'Контекст готов', packageDialogLines(data), [
        { label: 'Получить ответ', className: 'local', onClick: () => localAnswer() },
        { label: 'Подготовить для внешнего анализа', onClick: () => openExternalReview() },
        { label: 'Проверить источники', onClick: () => showInspectorTab('lore') }
      ]);
      setOutput(JSON.stringify(redactTechnicalPayload(data), null, 2));
    }

    function resetReviewPanel() {
      lastPackage = null;
      lastPrompt = "";
      const panel = document.getElementById('reviewPanel');
      const checkbox = document.getElementById('reviewConfirm');
      panel.hidden = true;
      checkbox.checked = false;
      checkbox.disabled = true;
      document.getElementById('scribeBtn').disabled = true;
      document.getElementById('scribePlanBtn').disabled = true;
      document.getElementById('scribeApplyBtn').disabled = true;
      document.getElementById('scribeDraftBtn').disabled = true;
      document.getElementById('scribeState').hidden = true;
      document.getElementById('scribeState').textContent = "";
      document.getElementById('scribePlanState').textContent = "";
      document.getElementById('scribeDetails').innerHTML = "";
      document.getElementById('scribePlanList').innerHTML = "";
      document.getElementById('rebuildPromptBtn').disabled = true;
      document.getElementById('reviewCopyBtn').disabled = true;
      document.getElementById('loreRebuildState').textContent = "";
      loreAvailableSources = [];
      currentScribePlan = null;
      document.getElementById('promptPreview').value = "";
      document.getElementById('reviewState').textContent = "-";
      panel.classList.remove('attention');
    }

    function renderReviewPanel(data) {
      const panel = document.getElementById('reviewPanel');
      const checkbox = document.getElementById('reviewConfirm');
      const state = document.getElementById('reviewState');
      const preview = document.getElementById('promptPreview');
      panel.hidden = false;
      checkbox.checked = false;
      checkbox.disabled = !!data.local_fallback_required || !data.safe_for_codex_after_confirmation;
      preview.value = data.prompt || "";
      if (data.local_fallback_required || !data.safe_for_codex_after_confirmation) {
        state.textContent = "локально";
        state.className = "review-state danger";
      } else if (hasManualConfirmationRisk(data)) {
        state.textContent = "нужен просмотр";
        state.className = "review-state";
      } else {
        state.textContent = "нужно подтверждение";
        state.className = "review-state";
      }
      updateCopyState();
    }

    function updateCopyState() {
      const checkbox = document.getElementById('reviewConfirm');
      const copyBtn = document.getElementById('copyBtn');
      const reviewCopyBtn = document.getElementById('reviewCopyBtn');
      const canCopy = !!lastPackage &&
        !!lastPackage.safe_for_codex_after_confirmation &&
        !lastPackage.local_fallback_required &&
        checkbox.checked;
      copyBtn.disabled = !canCopy;
      reviewCopyBtn.disabled = !canCopy;
      if (lastPackage && canCopy) {
        const state = document.getElementById('reviewState');
        state.textContent = "подтверждено";
        state.className = "review-state ok";
      }
      if (lastPackage && !checkbox.checked && lastPackage.safe_for_codex_after_confirmation && !lastPackage.local_fallback_required) {
        const state = document.getElementById('reviewState');
        state.textContent = hasManualConfirmationRisk(lastPackage) ? "нужен просмотр" : "нужно подтверждение";
        state.className = "review-state";
      }
    }

    function openExternalReview() {
      showInspectorTab('prompt');
      const panel = document.getElementById('reviewPanel');
      panel.classList.add('attention');
      window.setTimeout(() => panel.classList.remove('attention'), 1600);
      if (!lastPackage) return;
      if (lastPackage.local_fallback_required || !lastPackage.safe_for_codex_after_confirmation) {
        setProcessingSummary({
          badge: 'локально',
          state: 'Внешний анализ заблокирован.',
          context: packageContextLabel(lastPackage),
          external: 'Внешний маршрут заблокирован.',
          next: 'Используй локальный ответ или проверь персональные данные вручную.',
          reason: 'Пакет требует локального маршрута или ручной проверки ПД.'
        });
      }
    }

    function renderMaskDetails(data) {
      const root = document.getElementById('veilDetails');
      root.innerHTML = "";
      const reviews = veilReviewRows(data);
      if (reviews.length) root.appendChild(veilReviewTable(reviews));
      if (data.query_mask_review?.unresolved_reason) {
        root.appendChild(maskCard('Причина блокировки', data.query_mask_review.unresolved_reason, 'danger'));
      }
      if (data.prompt_mask_review?.unresolved_reason) {
        root.appendChild(maskCard('Итоговый prompt', data.prompt_mask_review.unresolved_reason, 'danger'));
      }
      for (const file of data.files || []) {
        const review = file.mask_review;
        if (review?.unresolved_reason) {
          root.appendChild(maskCard(`Файл: ${file.name}`, review.unresolved_reason, 'danger'));
        }
      }
      if (!root.children.length) {
        root.appendChild(emptyCard('Veil', 'Нет данных проверки для запроса или файлов.'));
      } else {
        root.appendChild(tokenNote(data));
      }
    }

    function veilReviewRows(data) {
      const rows = [];
      if (data.query_mask_review) rows.push({
        object: 'Запрос',
        review: data.query_mask_review,
        action: veilAction(data.query_mask_review, data)
      });
      if (data.prompt_mask_review) rows.push({
        object: 'Итоговый prompt',
        review: data.prompt_mask_review,
        action: veilAction(data.prompt_mask_review, data)
      });
      for (const file of data.files || []) {
        if (!file.mask_review) continue;
        rows.push({
          object: `Файл: ${file.name}`,
          review: file.mask_review,
          action: veilAction(file.mask_review, data)
        });
      }
      return rows;
    }

    function veilReviewTable(rows) {
      const wrap = document.createElement('div');
      wrap.className = 'veil-table-wrap';
      const table = document.createElement('table');
      table.className = 'veil-table';
      const head = document.createElement('thead');
      const headRow = document.createElement('tr');
      for (const title of ['Объект', 'Статус проверки', 'Всего замен', 'Категории', 'Токены', 'Есть неподтвержденный риск ПД', 'Действие']) {
        const th = document.createElement('th');
        th.textContent = title;
        headRow.appendChild(th);
      }
      head.appendChild(headRow);
      table.appendChild(head);
      const body = document.createElement('tbody');
      for (const row of rows) {
        const tr = document.createElement('tr');
        const cells = [
          row.object,
          row.review.status || '-',
          String(row.review.total_replacements || 0),
          formatCounts(row.review.counts),
          safeTokens(row.review),
          row.review.unresolved_pii ? 'да' : 'нет',
          row.action,
        ];
        for (const value of cells) {
          const td = document.createElement('td');
          td.textContent = value;
          if (value === 'да' || value === 'Требуется локально' || value === 'Нужна ручная проверка') td.className = 'danger';
          if (value === 'Ручное подтверждение') td.className = 'warn';
          if (value === 'Разрешено') td.className = 'ok';
          tr.appendChild(td);
        }
        body.appendChild(tr);
      }
      table.appendChild(body);
      wrap.appendChild(table);
      return wrap;
    }

    function tokenNote(data) {
      const note = document.createElement('div');
      note.className = 'token-note';
      note.textContent = safeTokenExplanation(data);
      return note;
    }

    function safeTokens(review) {
      const tokens = (review.findings || []).map((finding) => finding.token).filter(Boolean);
      if (tokens.length) return tokens.join(', ');
      const categories = Object.keys(review.counts || {}).sort();
      return categories.length ? categories.map((category) => `[${category}]`).join(', ') : '-';
    }

    function veilAction(review, data) {
      if (review.unresolved_pii) return 'Нужна ручная проверка';
      if (review.manual_confirmation_required) return 'Ручное подтверждение';
      if (data.local_fallback_required) return 'Требуется локально';
      return 'Разрешено';
    }

    function hasManualConfirmationRisk(data) {
      const reviews = [];
      if (data?.query_mask_review) reviews.push(data.query_mask_review);
      if (data?.prompt_mask_review) reviews.push(data.prompt_mask_review);
      for (const file of data?.files || []) {
        if (file.mask_review) reviews.push(file.mask_review);
      }
      return reviews.some((review) => !!review.manual_confirmation_required);
    }

    function updateScribeState(data) {
      const canCreateScribeDraft = !hasUnresolvedPii(data);
      const blocked = !!data.local_fallback_required || !canCreateScribeDraft;
      const button = document.getElementById('scribeBtn');
      const planButton = document.getElementById('scribePlanBtn');
      const draftButton = document.getElementById('scribeDraftBtn');
      const state = document.getElementById('scribeState');
      button.disabled = blocked;
      if (planButton) planButton.disabled = blocked;
      if (draftButton) draftButton.disabled = blocked;
      if (blocked) {
        state.textContent = scribeBlockedReason(data);
        state.hidden = false;
      } else {
        state.textContent = 'Обновление памяти доступно: можно предложить записи внутри Gaia.';
        state.className = 'action-note ok';
        state.hidden = false;
      }
      if (blocked) state.className = 'action-note danger';
    }

    function scribeBlockedReason(data) {
      if (hasUnresolvedPii(data)) return 'Обновление памяти заблокировано: есть неподтвержденный риск ПД.';
      if (data.local_fallback_required) return 'Обновление памяти заблокировано: контекст требует локальной обработки.';
      return 'Обновление памяти заблокировано для текущего контекста.';
    }

    function renderMemorySources(data) {
      const root = document.getElementById('loreDetails');
      const list = document.getElementById('loreSourceList');
      const button = document.getElementById('rebuildPromptBtn');
      const state = document.getElementById('loreRebuildState');
      root.innerHTML = "";
      list.innerHTML = "";
      state.textContent = "";
      const sources = data.memory_sources || [];
      const selectedIds = new Set(sources.map((source) => source.id).filter(Boolean));
      const availableSources = loreAvailableSources.length ? loreAvailableSources : sources;
      root.appendChild(maskCard(
        'Lore',
        sources.length
          ? `выбрано ${sources.length} из ${data.memory_total_sections || 0} разделов; группа: ${data.group_title || 'нет'}`
          : `подтвержденный контекст по запросу не найден (${sources.length} из ${data.memory_total_sections || 0} разделов). ${loreCoverageNextStep(data)}`,
        sources.length ? 'ok' : ''
      ));
      renderEvidencePlan(root, data.evidence_plan || []);
      for (const source of availableSources) {
        const terms = (source.matched_terms || []).join(', ') || 'нет';
        const row = document.createElement('label');
        row.className = 'lore-source';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'lore-source-checkbox';
        checkbox.value = source.id || "";
        checkbox.checked = selectedIds.has(source.id);
        checkbox.disabled = !source.id;
        const body = document.createElement('div');
        const title = document.createElement('b');
        title.textContent = source.heading || 'Без заголовка';
        const meta = document.createElement('span');
        meta.textContent = `${source.scope || 'project'}: ${source.project || '-'}; строки ${source.line_start}-${source.line_end}; score ${source.score}; совпадения: ${terms}`;
        body.appendChild(title);
        body.appendChild(meta);
        row.appendChild(checkbox);
        row.appendChild(body);
        list.appendChild(row);
      }
      button.disabled = !currentJobId || availableSources.length === 0;
    }

    function renderEvidencePlan(root, items) {
      if (!items.length) {
        root.appendChild(maskCard('Evidence', 'Lore не нашёл подходящих разделов — анализ источников не выполнялся.', ''));
        return;
      }
      if (items.length === 1 && items[0].status === 'missing' && (items[0].reason || '').includes('Lore не выбрал разделы')) {
        root.appendChild(maskCard('Evidence', 'Lore не нашёл подходящих разделов — анализ источников не выполнялся.', 'warn'));
        return;
      }
      const confirmed = items.filter((item) => item.status === 'confirmed').length;
      const missing = items.filter((item) => item.status !== 'confirmed');
      const detail = missing.length
        ? ` Неполное покрытие: ${missing.map((item) => evidenceItemLabel(item)).join('; ')}.`
        : ' Все проверенные фрагменты подтверждены.';
      const summary = `Подтверждено ${confirmed} из ${items.length}.${detail}`;
      root.appendChild(maskCard('Evidence', summary, confirmed ? 'ok' : 'warn'));
    }

    function evidenceItemLabel(item) {
      const status = item.status === 'missing' ? 'не найдено' : (item.status === 'partial' ? 'частично' : item.status || 'нет статуса');
      const heading = item.heading && item.heading !== '-' ? item.heading : '';
      const reason = item.reason && item.reason !== '-' ? item.reason : '';
      return [status, heading, reason].filter(Boolean).join(': ') || status;
    }

    async function loadScribeInbox() {
      const project = document.getElementById('project')?.value || "";
      const list = document.getElementById('scribeInboxList');
      const preview = document.getElementById('scribeInboxPreview');
      if (!list || !preview || !project) return;
      renderScribeScope(project, null);
      list.innerHTML = "";
      preview.textContent = 'Gaia загружает новые файлы проекта...';
      setScribeInboxState('', true);
      const res = await fetch(`/api/scribe-inbox?project=${encodeURIComponent(project)}`);
      const data = await res.json();
      if (!res.ok) {
        list.appendChild(emptyCard('Файлы', errorMessage(data.error, 'Не удалось загрузить список файлов.')));
        preview.textContent = errorMessage(data.error, 'Не удалось загрузить список файлов.');
        return;
      }
      scribeInboxItems = data.items || [];
      if (!currentScribeInboxItem || currentScribeInboxItem.project !== project) {
        currentScribeInboxItem = scribeInboxItems[0] || null;
      } else {
        currentScribeInboxItem = scribeInboxItems.find((item) => item.id === currentScribeInboxItem.id) || scribeInboxItems[0] || null;
      }
      renderScribeInboxList();
      if (currentScribeInboxItem) {
        renderScribeScope(project, currentScribeInboxItem);
        await previewScribeInboxItem(currentScribeInboxItem);
      } else {
        renderScribeScope(project, null);
        preview.textContent = 'Новых файлов для обработки не найдено. Уже записанные источники скрыты из списка.';
      }
      updateScribeInboxButtons();
    }

    function renderScribeInboxList() {
      const list = document.getElementById('scribeInboxList');
      if (!list) return;
      list.innerHTML = "";
      if (!scribeInboxItems.length) {
        list.appendChild(emptyCard('Файлы', 'Нет новых файлов для обработки. Уже записанные источники скрыты.'));
        return;
      }
      for (const item of scribeInboxItems) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `inbox-item ${currentScribeInboxItem?.id === item.id ? 'active' : ''}`.trim();
        const title = document.createElement('b');
        title.textContent = item.name || item.relative_path;
        const meta = document.createElement('span');
        meta.textContent = `${item.kind}; ${formatBytes(item.size || 0)}; ${item.status}; ${item.relative_path}`;
        button.appendChild(title);
        button.appendChild(meta);
        button.onclick = async () => {
          currentScribeInboxItem = item;
          clearScribePlanForNewSource();
          renderScribeInboxList();
          updateScribeInboxButtons();
          renderScribeScope(document.getElementById('project').value, item);
          await previewScribeInboxItem(item);
        };
        list.appendChild(button);
      }
    }

    async function previewScribeInboxItem(item) {
      const preview = document.getElementById('scribeInboxPreview');
      if (!item || !preview) return;
      preview.textContent = 'Gaia готовит предварительный просмотр...';
      const project = document.getElementById('project').value;
      const res = await fetch(`/api/scribe-inbox/preview?project=${encodeURIComponent(project)}&path=${encodeURIComponent(item.relative_path)}`);
      const data = await res.json();
      if (!res.ok) {
        preview.textContent = errorMessage(data.error, 'Предварительный просмотр недоступен.');
        return;
      }
      if (data.excel?.normalized_markdown) {
        preview.textContent = `Будет прочитан файл: ${item.relative_path}\n\n${data.excel.normalized_markdown}`;
      } else {
        preview.textContent = `Будет прочитан файл: ${item.relative_path}\n\n${data.preview_text || 'Предварительный просмотр доступен после разбора файла.'}`;
      }
    }

    function renderScribeScope(project, item) {
      const projectNode = document.getElementById('scribeProjectScope');
      const readNode = document.getElementById('scribeReadScope');
      if (projectNode) projectNode.textContent = project || '-';
      if (!readNode) return;
      if (!item) {
        readNode.textContent = 'Ничего не читается автоматически. Выбери файл в списке.';
        return;
      }
      readNode.textContent = `${item.relative_path} будет прочитан только после кнопки Разобрать выбранный файл.`;
    }

    async function prepareInboxPackage() {
      if (!currentScribeInboxItem) return;
      const state = document.getElementById('scribeInboxState');
      state.textContent = 'Gaia разбирает выбранный файл...';
      state.className = 'action-note';
      const project = document.getElementById('project').value;
      clearScribePlanForNewSource();
      const res = await fetch('/api/scribe-inbox/package', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project,
          path: currentScribeInboxItem.relative_path,
          profile: document.getElementById('profile').value,
        })
      });
      const data = await res.json();
      if (!res.ok) {
        setScribeInboxState(errorMessage(data.error, 'Не удалось разобрать выбранный файл.'), false);
        setOutput(JSON.stringify(data, null, 2));
        return;
      }
      currentJobId = data.package?.run_id || "";
      renderPackage(data.package);
      setScribeInboxState('Файл разобран. Теперь можно предложить записи в память.', true);
      setOutput(JSON.stringify(redactTechnicalPayload(data), null, 2));
      await loadScribeInbox();
      showScreen('scribe');
    }

    async function ignoreInboxItem() {
      if (!currentScribeInboxItem) return;
      const project = document.getElementById('project').value;
      const res = await fetch('/api/scribe-inbox/ignore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project, path: currentScribeInboxItem.relative_path })
      });
      const data = await res.json();
      if (!res.ok) {
        setScribeInboxState(errorMessage(data.error, 'Не удалось исключить файл.'), false);
        return;
      }
      currentScribeInboxItem = null;
      setScribeInboxState('Файл скрыт из списка.', true);
      await loadScribeInbox();
    }

    function clearScribePlanForNewSource() {
      currentScribePlan = null;
      const list = document.getElementById('scribePlanList');
      const details = document.getElementById('scribeDetails');
      const state = document.getElementById('scribePlanState');
      const applyBtn = document.getElementById('scribeApplyBtn');
      if (list) list.innerHTML = "";
      if (details) details.innerHTML = "";
      if (state) {
        state.textContent = 'Выбран другой файл. Разбери его и предложи новые записи в память.';
        state.className = 'action-note';
      }
      if (applyBtn) applyBtn.disabled = true;
    }

    function updateScribeInboxButtons() {
      const hasItem = !!currentScribeInboxItem;
      const packageBtn = document.getElementById('scribeInboxPackageBtn');
      const ignoreBtn = document.getElementById('scribeInboxIgnoreBtn');
      if (packageBtn) packageBtn.disabled = !hasItem;
      if (ignoreBtn) ignoreBtn.disabled = !hasItem;
    }

    function setScribeInboxState(message, ok) {
      const state = document.getElementById('scribeInboxState');
      if (!state) return;
      state.textContent = message;
      state.className = ok ? 'action-note ok' : 'action-note danger';
    }

    function formatBytes(size) {
      if (size < 1024) return `${size} B`;
      if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
      return `${(size / 1024 / 1024).toFixed(1)} MB`;
    }

    function renderScribePlan(plan) {
      currentScribePlan = plan;
      const details = document.getElementById('scribeDetails');
      const list = document.getElementById('scribePlanList');
      const applyBtn = document.getElementById('scribeApplyBtn');
      const state = document.getElementById('scribePlanState');
      details.innerHTML = "";
      list.innerHTML = "";
      state.textContent = "";
      if (!plan) {
        details.appendChild(maskCard('Scribe', 'Предложения в память еще не построены.', ''));
        applyBtn.disabled = true;
        return;
      }
      details.appendChild(maskCard('Scribe', `${plan.status}; записей: ${(plan.items || []).length}; backup перед записью`, plan.status === 'ready' ? 'ok' : 'warn'));
      if (plan.blocked_reason) details.appendChild(maskCard('Блокировка', plan.blocked_reason, 'danger'));
      for (const note of plan.safety_notes || []) {
        details.appendChild(maskCard('Safety', note, ''));
      }
      for (const item of plan.items || []) {
        const row = document.createElement('label');
        row.className = 'lore-source';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'scribe-plan-checkbox';
        checkbox.value = item.id || "";
        checkbox.checked = !!item.selected;
        checkbox.disabled = !item.selected || item.destination === 'exclude';
        checkbox.onchange = updateScribeApplyState;
        const body = document.createElement('div');
        const title = document.createElement('b');
        title.textContent = item.title || item.category || 'Запись памяти';
        const meta = document.createElement('span');
        meta.textContent = `${item.category}; ${item.operation}; ${item.destination || '-'}; ${item.confidence}; ${item.target_path || '-'}`;
        const text = document.createElement('pre');
        text.className = 'scribe-plan-body';
        text.textContent = item.body || "";
        const evidence = document.createElement('span');
        evidence.textContent = item.evidence ? `evidence: ${item.evidence}` : 'evidence: -';
        body.appendChild(title);
        body.appendChild(meta);
        body.appendChild(text);
        body.appendChild(evidence);
        if ((item.safety_notes || []).length) {
          const safety = document.createElement('span');
          safety.className = 'danger';
          safety.textContent = `проверка: ${(item.safety_notes || []).join('; ')}`;
          body.appendChild(safety);
        }
        row.appendChild(checkbox);
        row.appendChild(body);
        list.appendChild(row);
      }
      updateScribeApplyState();
    }

    function updateScribeApplyState() {
      const applyBtn = document.getElementById('scribeApplyBtn');
      const selected = Array.from(document.querySelectorAll('.scribe-plan-checkbox'))
        .filter((item) => item.checked && !item.disabled);
      applyBtn.disabled = !currentJobId || !currentScribePlan || selected.length === 0 || currentScribePlan.status !== 'ready';
    }

    async function rebuildPromptWithSelectedLore() {
      if (!lastPackage || !currentJobId) return;
      const selected = Array.from(document.querySelectorAll('.lore-source-checkbox'))
        .filter((item) => item.checked && item.value)
        .map((item) => item.value);
      const button = document.getElementById('rebuildPromptBtn');
      const state = document.getElementById('loreRebuildState');
      button.disabled = true;
      state.textContent = 'Gaia пересобирает контекст по выбранной памяти...';
      try {
        const res = await fetch('/api/rebuild-prompt', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ job_id: currentJobId, selected_memory_source_ids: selected })
        });
        const data = await res.json();
        if (!res.ok) {
          state.textContent = errorMessage(data.error, 'Не удалось пересобрать prompt.');
          state.className = 'action-note danger';
          button.disabled = false;
          return;
        }
        lastPackage = {
          ...lastPackage,
          prompt: data.prompt,
          memory_chars: data.memory_chars,
          memory_sources: data.memory_sources || [],
          evidence_plan: data.evidence_plan || lastPackage.evidence_plan || [],
          memory_total_sections: data.memory_total_sections,
          group_code: data.group_code || lastPackage.group_code || "",
          group_title: data.group_title || lastPackage.group_title || "",
          group_sections: data.group_sections || lastPackage.group_sections || 0,
          rebuild_id: data.rebuild_id
        };
        lastPrompt = data.prompt || "";
        document.getElementById('memory').textContent = `${lastPackage.memory_chars} зн.`;
        renderMemorySources(lastPackage);
        currentScribePlan = null;
        renderScribePlan(null);
        renderReviewPanel(lastPackage);
        renderPackageSummary(lastPackage);
        setOutput(JSON.stringify(redactTechnicalPayload(lastPackage), null, 2));
        state.textContent = 'Контекст пересобран по выбранным разделам памяти.';
        state.className = 'action-note ok';
        addDialogCard('gaia', 'Контекст пересобран', [
          loreCoverageLabel(lastPackage),
          loreCoverageNextStep(lastPackage),
          'Материалы для ответа обновлены.'
        ], [
          { label: 'Проверить подготовленные материалы', onClick: () => showInspectorTab('prompt') },
          { label: 'Получить ответ', className: 'local', onClick: () => localAnswer() }
        ]);
        setSummaryDetails([
          'Контекст пересобран по выбранным разделам памяти.',
          loreCoverageLabel(lastPackage),
          loreCoverageNextStep(lastPackage),
          lastPackage.safe_for_codex_after_confirmation && !lastPackage.local_fallback_required
            ? 'Следующий шаг: если нужен внешний маршрут, проверь очищенный пакет и поставь галочку.'
            : 'Следующий шаг: используй локальный маршрут или ручную проверку.'
        ]);
      } catch (error) {
        state.textContent = 'Не удалось пересобрать prompt.';
        state.className = 'action-note danger';
        setProcessingSummary({
          badge: 'ошибка',
          state: 'Не удалось пересобрать prompt.',
          context: currentContextLabel(),
          external: 'Без изменений.',
          next: 'Проверь выбранные разделы памяти и попробуй снова.',
          reason: 'Не удалось пересобрать prompt.'
        });
      } finally {
        button.disabled = !(lastPackage && loreAvailableSources.length);
      }
    }

    function renderConversationLocalSummary(data) {
      const packageData = data.package || {};
      const localResult = data.local_result || {};
      const ok = !!localResult.ok;
      const veilText = `Проверка данных: ${packageData.query_mask_status || '-'}, замен ${packageData.query_mask_replacements || 0}.`;
      const external = ok
        ? 'Внешний маршрут не использовался.'
        : 'Внешний маршрут не использовался; локальный ответ не получен.';
      const reason = !ok
        ? errorMessage(localResult.error, 'Локальный ответ не вернулся.')
        : (hasUnresolvedPii(packageData) ? blockReason(packageData) : '');
      setProcessingSummary({
        badge: ok ? 'локально' : 'ошибка',
        state: ok ? 'Локальный ответ получен в продолжении диалога.' : 'Локальный ответ не получен.',
        context: packageContextLabel(packageData),
        external,
        next: ok ? 'Проверь ответ. Проектная память не менялась автоматически.' : 'Проверь локальный ответ и повтори.',
        reason
      });
      setSummaryDetails([
        ok ? 'Продолжение диалога обработано локально.' : 'Продолжение диалога сохранено, но локальный ответ не получен.',
        external,
        veilText,
        loreCoverageLabel(packageData),
        loreCoverageNextStep(packageData),
        'Проектная память не менялась автоматически.'
      ]);
    }

    function renderPackageSummary(data) {
      const blocked = !!data.local_fallback_required || !data.safe_for_codex_after_confirmation;
      const reason = blockReason(data);
      const manualRisk = hasManualConfirmationRisk(data);
      const next = blocked
        ? (hasUnresolvedPii(data) ? 'Ответить локально или исправить запрос после ручной проверки.' : 'Ответить локально.')
        : (manualRisk ? 'Открой подготовленный запрос, проверь очищенный пакет и подтверди отправку.' : 'Если нужен внешний маршрут, проверить очищенный пакет; можно также ответить локально.');
      setProcessingSummary({
        badge: blocked ? 'локально' : 'готов',
        state: blocked ? 'Требуется локальная обработка.' : 'Контекст готов.',
        context: packageContextLabel(data),
        external: blocked ? 'Внешний маршрут заблокирован.' : externalRouteReadyText(),
        next,
        reason: blocked ? reason : ''
      });
      setSummaryDetails([
        blocked ? 'Внешний маршрут заблокирован: контекст нельзя копировать наружу.' : (manualRisk ? 'Найден тематический риск ПД: проверь очищенный пакет и подтверди, что конкретные ПД не видны.' : `${externalRouteReadyText()} Сначала проверь запрос и поставь галочку.`),
        `Проект: ${data.project || '-'}`,
        `Группа: ${data.group_title || 'без группы'}`,
        `Профиль: ${data.profile_title || data.profile_id || '-'}`,
        loreCoverageLabel(data),
        loreCoverageNextStep(data),
        `Проверка данных: ${data.query_mask_status || '-'}, замен ${data.query_mask_replacements || 0}.`
      ]);
    }

    function renderReadableError(message, payload) {
      showTab('summary');
      setProcessingSummary({
        badge: 'ошибка',
        state: 'Ошибка обработки.',
        context: currentContextLabel(),
        external: 'Внешний маршрут заблокирован.',
        next: 'Исправить запрос или конфигурацию и запустить заново.',
        reason: message
      });
      setSummaryDetails([message]);
      addDialogCard('error', 'Ошибка обработки', message, [
        { label: 'Показать технические данные', onClick: () => showInspectorTab('json') }
      ]);
      const notes = document.getElementById('notes');
      notes.innerHTML = "";
      const li = document.createElement('li');
      li.textContent = message;
      li.className = 'danger';
      notes.appendChild(li);
      setOutput(JSON.stringify(payload || { error: message }, null, 2));
    }

    function setProcessingSummary(data) {
      document.getElementById('summaryBadge').textContent = data.badge || '-';
      document.getElementById('summaryState').textContent = data.state || '-';
      document.getElementById('summaryContext').textContent = data.context || '-';
      document.getElementById('summaryExternal').textContent = data.external || '-';
      document.getElementById('summaryNext').textContent = data.next || '-';
      const reason = document.getElementById('summaryReason');
      reason.hidden = !data.reason;
      reason.textContent = data.reason || "";
    }

    function setSummaryDetails(lines) {
      const root = document.getElementById('summaryDetails');
      root.innerHTML = "";
      const list = document.createElement('ul');
      list.className = 'notes';
      for (const line of lines) {
        const li = document.createElement('li');
        li.textContent = line;
        list.appendChild(li);
      }
      root.appendChild(list);
    }

    function blockReason(data) {
      const notes = data.policy_notes || [];
      const unresolved = unresolvedReason(data);
      if (unresolved) return unresolved;
      return notes[0] || 'Требуется локальная обработка по правилам маршрута.';
    }

    function hasUnresolvedPii(data) {
      return !!unresolvedReason(data);
    }

    function unresolvedReason(data) {
      const reviews = [];
      if (data.query_mask_review) reviews.push(data.query_mask_review);
      if (data.prompt_mask_review) reviews.push(data.prompt_mask_review);
      for (const file of data.files || []) {
        if (file.mask_review) reviews.push(file.mask_review);
      }
      const review = reviews.find((item) => item.unresolved_pii);
      return review ? (review.unresolved_reason || 'Есть риск ПД без надежной замены.') : "";
    }

    function packageContextLabel(data) {
      const group = data.group_title ? `${data.group_title} / ` : "";
      return `${group}${data.project || '-'} / ${data.profile_title || data.profile_id || '-'}`;
    }

    function currentContextLabel() {
      const projectRecord = selectedProjectRecord();
      const group = projectRecord?.group_title ? `${projectRecord.group_title} / ` : "";
      const project = projectRecord?.title || document.getElementById('project').value || '-';
      const profile = document.getElementById('profile').selectedOptions[0]?.textContent || '-';
      return `${group}${project} / ${profile}`;
    }

    function showTab(name) {
      for (const panel of document.querySelectorAll('.tab-panel')) {
        panel.hidden = panel.id !== `panel-${name}`;
      }
      for (const tab of document.querySelectorAll('.tab-button')) {
        tab.classList.toggle('active', tab.id === `tab-${name}`);
      }
      if (name === 'scribe') loadScribeInbox();
    }

    function maskCard(title, text, className) {
      const card = document.createElement('div');
      card.className = 'mask-card';
      const label = document.createElement('b');
      label.textContent = title;
      const value = document.createElement('span');
      value.textContent = text;
      if (className) value.className = className;
      card.appendChild(label);
      card.appendChild(value);
      return card;
    }

    function emptyCard(title, text) {
      return maskCard(title, text);
    }

    function formatCounts(counts) {
      if (!counts || Object.keys(counts).length === 0) return 'нет';
      return Object.keys(counts).sort().map((key) => `${key}: ${counts[key]}`).join(', ');
    }

    async function localAnswer() {
      if (!lastPrompt) return;
      const status = await checkLocalStatus();
      if (!status.available) {
        if (status.status === 'timeout') {
          showLocalHealthCheckTimedOut(status.message);
        } else {
          showLocalUnavailable(status.message);
        }
        return;
      }
      localAnswerCanceled = false;
      localAnswerTimedOut = false;
      localAnswerController = new AbortController();
      document.getElementById('localBtn').disabled = true;
      document.getElementById('cancelLocalBtn').disabled = false;
      setLocalStatus('Локальный ответ выполняется.', 'ok');
      addDialogCard('gaia', 'Ответ выполняется', 'Ответ готовится на этом компьютере. Внешний маршрут не используется.');
      setOutput(JSON.stringify({
        status: 'local_answer_running',
        endpoint: '/api/local-answer',
        external_route_used: false
      }, null, 2));
      localAnswerTimeout = window.setTimeout(() => {
        localAnswerTimedOut = true;
        if (localAnswerController) localAnswerController.abort();
      }, LOCAL_ANSWER_TIMEOUT_MS);
      try {
        const res = await fetch('/api/local-answer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: lastPrompt }),
          signal: localAnswerController.signal
        });
        const data = await res.json();
        if (data.ok) {
          setLocalStatus('Ответ получен.', 'ok');
          setProcessingSummary({
            badge: 'локально',
            state: 'Локальный ответ получен.',
            context: lastPackage ? packageContextLabel(lastPackage) : currentContextLabel(),
            external: lastPackage?.local_fallback_required ? 'Внешний маршрут заблокирован.' : 'Внешний маршрут не использовался.',
            next: 'Проверь ответ. Проектная память не менялась автоматически.'
          });
          setSummaryDetails([
            'Ответ получен локально.',
            'Следующий шаг: используй ответ или уточни задачу и подготовь материалы заново.',
            'Проектная память не менялась автоматически.'
          ]);
          addDialogCard('local-answer', 'Локальный ответ', data.structured_answer || data.answer || 'Локальный ответ пуст.', [
            { label: 'Уточнить запрос', onClick: () => document.getElementById('query').focus() },
            { label: 'Сохранить выводы в память', onClick: () => createScribePlan() },
            { label: 'Показать подготовленный запрос', onClick: () => showInspectorTab('prompt') }
          ]);
          addDialogCard('gaia', 'Защита данных', safeTokenExplanation(lastPackage));
          setOutput(JSON.stringify(redactTechnicalPayload(data), null, 2));
        } else if (data.status === 'timeout') {
          showLocalAnswerTimedOut(errorMessage(data.error, 'Локальный ответ не завершился вовремя.'));
        } else {
          showLocalUnavailable(errorMessage(data.error, 'Локальный ответ недоступен. Используй внешний маршрут только после проверки данных.'));
        }
      } catch (error) {
        if (localAnswerCanceled) {
          showLocalCanceled();
        } else if (localAnswerTimedOut) {
          showLocalAnswerTimedOut('Локальный ответ не завершился за отведенное время. Он может еще выполняться или быть занят.');
        } else {
          showLocalUnavailable('Локальный ответ недоступен. Используй внешний маршрут только после проверки данных.');
        }
      } finally {
        finishLocalAnswer();
      }
    }

    async function checkLocalStatus() {
      setLocalStatus('Проверяется...', '');
      try {
        const res = await fetch('/api/local-status');
        const data = await res.json();
        if (data.available) {
          setLocalStatus('Локальный ответ доступен.', 'ok');
        } else if (data.status === 'timeout') {
          setLocalStatus('Локальный ответ сейчас занят. Повтори чуть позже.', 'warn');
        } else {
          setLocalStatus('Локальный ответ недоступен.', 'danger');
        }
        setHeaderLmState(data);
        return data;
      } catch (error) {
        const data = { available: false, message: 'Локальный ответ недоступен.' };
        setLocalStatus('Локальный ответ недоступен.', 'danger');
        setHeaderLmState(data);
        return data;
      }
    }

    function cancelLocalAnswer() {
      localAnswerCanceled = true;
      if (localAnswerController) localAnswerController.abort();
      showLocalCanceled();
      finishLocalAnswer();
    }

    function finishLocalAnswer() {
      if (localAnswerTimeout) window.clearTimeout(localAnswerTimeout);
      localAnswerTimeout = null;
      localAnswerController = null;
      document.getElementById('cancelLocalBtn').disabled = true;
      document.getElementById('localBtn').disabled = !lastPrompt;
    }

    function showLocalCanceled() {
      const text = 'Локальный ответ остановлен. Обработка на компьютере могла еще продолжаться некоторое время.';
      setLocalStatus(text, 'warn');
      setProcessingSummary({
        badge: 'отменено',
        state: 'Локальный запрос отменен.',
        context: lastPackage ? packageContextLabel(lastPackage) : currentContextLabel(),
        external: lastPackage?.local_fallback_required ? 'Внешний маршрут заблокирован.' : 'Внешний маршрут не использовался.',
        next: 'Можно запустить локальный ответ снова или проверить внешний маршрут после проверки данных.',
        reason: text
      });
      setSummaryDetails([text, 'Следующий шаг: запусти локальный ответ снова или вернись к проверке запроса для модели.']);
      addDialogCard('error', 'Локальный запрос отменен', text);
      setOutput(text);
    }

    function showLocalHealthCheckTimedOut(message) {
      const text = message || 'Локальный ответ сейчас занят.';
      setLocalStatus(text, 'warn');
      setProcessingSummary({
        badge: 'проверка долгая',
        state: 'Локальный ответ не подтвердил готовность.',
        context: lastPackage ? packageContextLabel(lastPackage) : currentContextLabel(),
        external: lastPackage?.local_fallback_required ? 'Внешний маршрут заблокирован.' : 'Внешний маршрут не использовался.',
        next: 'Если локальная модель сейчас генерирует, дождись окончания и повтори ответ.',
        reason: text
      });
      setSummaryDetails([
        text,
        'Это не означает, что локальный ответ выключен: проверка могла попасть в занятую модель.'
      ]);
      addDialogCard('error', 'Локальный ответ не готов', text);
      setOutput(text);
    }

    function showLocalAnswerTimedOut(message) {
      const text = message || 'Локальный ответ не завершился за отведенное время. Он может еще выполняться или быть занят.';
      setLocalStatus(text, 'warn');
      setProcessingSummary({
        badge: 'локально долго',
        state: 'Локальный ответ не успел завершиться.',
        context: lastPackage ? packageContextLabel(lastPackage) : currentContextLabel(),
        external: lastPackage?.local_fallback_required ? 'Внешний маршрут заблокирован.' : 'Внешний маршрут не использовался.',
        next: 'Если генерация еще идет, дождись окончания или повтори ответ с более короткой задачей.',
        reason: text
      });
      setSummaryDetails([
        text,
        'Это не означает, что локальный ответ выключен: модель могла быть занята или медленно генерировать ответ.'
      ]);
      addDialogCard('error', 'Локальный ответ не успел завершиться', text);
      setOutput(text);
    }

    function showLocalUnavailable(message) {
      const text = message || 'Локальный ответ недоступен. Используй внешний маршрут только после проверки данных.';
      setLocalStatus(text, 'danger');
      setProcessingSummary({
        badge: 'локально недоступно',
        state: 'Локальный ответ недоступен.',
        context: lastPackage ? packageContextLabel(lastPackage) : currentContextLabel(),
        external: lastPackage?.local_fallback_required ? 'Внешний маршрут заблокирован.' : 'Возможен только после проверки данных.',
        next: 'Повтори локальный ответ или используй внешний маршрут только после проверки данных.',
        reason: text
      });
      setSummaryDetails([text, 'Следующий шаг: повтори локальный ответ.']);
      addDialogCard('error', 'Локальный ответ недоступен', text);
      setOutput(text);
    }

    function setLocalStatus(message, className) {
      const root = document.getElementById('localStatus');
      root.textContent = message;
      root.className = `local-status ${className || ''}`.trim();
    }

    async function createScribePlan() {
      if (!currentJobId || !lastPackage) return;
      showScreen('scribe');
      const state = document.getElementById('scribePlanState');
      state.textContent = 'Gaia предлагает записи в память...';
      state.className = 'action-note';
      const res = await fetch('/api/scribe-plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: currentJobId, package: lastPackage })
      });
      const data = await res.json();
      if (!res.ok) {
        renderScribePlan(data.status ? data : null);
        state.textContent = errorMessage(data.error, data.blocked_reason || 'Gaia не смогла предложить записи в память.');
        state.className = 'action-note danger';
        setOutput(JSON.stringify(data, null, 2));
        return;
      }
      renderScribePlan(data);
      state.textContent = 'Предложения готовы: проверь карточки и запиши выбранное в память.';
      state.className = 'action-note ok';
      addDialogCard('gaia', 'Предложения в память готовы', [
        `Карточек: ${(data.items || []).length}`,
        'Проектная память пока не менялась.',
        'Следующий шаг: проверь вкладку Память и запиши выбранное.'
      ], [
        { label: 'Открыть память', onClick: () => showScreen('scribe') }
      ]);
      setOutput(JSON.stringify(redactTechnicalPayload(data), null, 2));
    }

    async function applyScribePlan() {
      if (!currentJobId || !currentScribePlan) return;
      const selected = Array.from(document.querySelectorAll('.scribe-plan-checkbox'))
        .filter((item) => item.checked && !item.disabled)
        .map((item) => item.value);
      const state = document.getElementById('scribePlanState');
      state.textContent = 'Gaia записывает выбранное в память...';
      state.className = 'action-note';
      const res = await fetch('/api/scribe-apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: currentJobId, package: lastPackage, selected_item_ids: selected })
      });
      const data = await res.json();
      if (!res.ok) {
        state.textContent = errorMessage(data.error, 'Gaia не смогла записать выбранное в память.');
        state.className = 'action-note danger';
        setOutput(JSON.stringify(data, null, 2));
        return;
      }
      state.textContent = `Gaia записала ${data.applied.length} карточек; backup: ${data.backup_path}`;
      state.className = 'action-note ok';
      currentScribePlan = null;
      const applyBtn = document.getElementById('scribeApplyBtn');
      if (applyBtn) applyBtn.disabled = true;
      const details = document.getElementById('scribeDetails');
      const list = document.getElementById('scribePlanList');
      if (details && list) {
        details.innerHTML = "";
        list.innerHTML = "";
        details.appendChild(maskCard('Запись в память', `Готово: записано ${data.applied.length}; backup создан.`, 'ok'));
        details.appendChild(maskCard('Следующий шаг', 'Выбери другой файл, разбери его и предложи новые записи в память.', ''));
      }
      setProcessingSummary({
        badge: 'память',
        state: 'Память обновлена.',
        context: lastPackage ? packageContextLabel(lastPackage) : currentContextLabel(),
        external: 'Не требуется.',
        next: 'Проверь журнал памяти и при необходимости задай контрольный вопрос по проекту.',
      });
      setSummaryDetails([
        `Применено карточек: ${data.applied.length}.`,
        `Backup: ${data.backup_path}`,
        `Измененные файлы: ${(data.changed_files || []).join(', ') || '-'}`,
        data.retrieval_check || 'Проверь retrieval после обновления.'
      ]);
      addDialogCard('gaia', 'Память обновлена', [
        `Применено: ${data.applied.length}`,
        `Backup: ${data.backup_path}`,
        'Память обновлена только после подтверждения выбранных карточек.'
      ], [
        { label: 'Показать технические данные', onClick: () => showInspectorTab('json') }
      ]);
      setOutput(JSON.stringify(redactTechnicalPayload(data), null, 2));
    }

    async function createScribeDraft() {
      if (!currentJobId || !lastPackage) return;
      setOutput('Gaia сохраняет черновик обновления памяти без записи...');
      const res = await fetch('/api/scribe-draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: currentJobId, package: lastPackage })
      });
      const data = await res.json();
      if (!res.ok) {
        renderReadableError(errorMessage(data.error, 'Gaia не смогла сохранить черновик памяти.'), data);
        return;
      }
      const notes = document.getElementById('notes');
      const li = document.createElement('li');
      li.textContent = `Gaia создала черновик: ${data.draft_path}`;
      li.className = 'ok';
      notes.appendChild(li);
      setProcessingSummary({
        badge: 'память',
        state: 'Черновик памяти создан.',
        context: lastPackage ? packageContextLabel(lastPackage) : currentContextLabel(),
        external: lastPackage?.local_fallback_required ? 'Внешний маршрут заблокирован.' : 'Не требуется.',
        next: 'Можно использовать markdown-черновик как экспорт; основная запись доступна во вкладке Память.'
      });
      setSummaryDetails([
        `Gaia создала черновик: ${data.draft_path}`,
        'Проектная память не менялась автоматически.',
        'Следующий шаг: используй вкладку Память для записи внутри Gaia или проверь markdown вручную.'
      ]);
      addDialogCard('gaia', 'Черновик памяти создан', [
        `Путь: ${data.draft_path}`,
        'Проектная память не менялась автоматически.'
      ], [
        { label: 'Показать технические данные', onClick: () => showInspectorTab('json') }
      ]);
      setOutput(data.markdown || JSON.stringify(data, null, 2));
    }

    async function copyPrompt() {
      if (!lastPrompt) return;
      const checkbox = document.getElementById('reviewConfirm');
      if (!lastPackage || lastPackage.local_fallback_required || !lastPackage.safe_for_codex_after_confirmation || !checkbox.checked) {
        setProcessingSummary({
          badge: 'заблокировано',
          state: 'Копирование заблокировано.',
          context: lastPackage ? packageContextLabel(lastPackage) : currentContextLabel(),
          external: 'Внешний маршрут заблокирован.',
          next: 'Проверь запрос для модели, убедись что ПД не видны, затем поставь галочку подтверждения.',
          reason: 'Копирование заблокировано: сначала проверь очищенный пакет и поставь галочку.'
        });
        setSummaryDetails(['Копирование заблокировано: сначала проверь очищенный пакет и поставь галочку.']);
        return;
      }
      const copied = await copyTextToClipboard(lastPrompt);
      if (!copied) {
        setProcessingSummary({
          badge: 'ошибка',
          state: 'Копирование не выполнено.',
          context: packageContextLabel(lastPackage),
          external: 'Без изменений.',
          next: 'Открой вкладку Запрос для модели и скопируй текст вручную.',
          reason: 'Браузер запретил доступ к буферу обмена.'
        });
        setSummaryDetails(['Браузер запретил доступ к буферу обмена. Открой вкладку Запрос для модели и скопируй текст вручную.']);
        addDialogCard('error', 'Копирование не выполнено', 'Браузер запретил доступ к буферу обмена. Запрос остается доступен в Диагностике.', [
          { label: 'Показать запрос', onClick: () => showInspectorTab('prompt') }
        ]);
        return;
      }
      setProcessingSummary({
        badge: 'скопировано',
        state: 'Запрос скопирован.',
        context: packageContextLabel(lastPackage),
        external: 'Разрешен после проверки.',
        next: 'Вставь запрос во внешний контур. Проектная память не менялась автоматически.'
      });
      setSummaryDetails(['Запрос скопирован. Внешний анализ запускай только после ручной проверки.']);
      addDialogCard('gaia', 'Запрос скопирован', 'Запрос скопирован после ручной проверки. Проектная память не менялась автоматически.');
    }

    async function copyTextToClipboard(text) {
      if (navigator?.clipboard?.writeText) {
        try {
          await Promise.race([
            navigator.clipboard.writeText(text),
            new Promise((_, reject) => window.setTimeout(() => reject(new Error('clipboard timeout')), 1500))
          ]);
          return true;
        } catch (error) {
          // Fall through to the textarea copy path.
        }
      }
      const textarea = document.createElement('textarea');
      textarea.value = text;
      textarea.setAttribute('readonly', 'true');
      textarea.style.position = 'fixed';
      textarea.style.left = '-9999px';
      textarea.style.top = '0';
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      let copied = false;
      try {
        copied = document.execCommand('copy');
      } catch (error) {
        copied = false;
      } finally {
        document.body.removeChild(textarea);
      }
      return copied;
    }

    async function launchModule(name) {
      const res = await fetch('/api/launch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ module: name })
      });
      const data = await res.json();
      setOutput(JSON.stringify(data, null, 2));
    }

    function setOutput(value) {
      document.getElementById('output').textContent = value;
    }

    function redactTechnicalPayload(value) {
      if (Array.isArray(value)) return value.map(redactTechnicalPayload);
      if (!value || typeof value !== 'object') return value;
      const result = {};
      for (const key of Object.keys(value)) {
        if (key === 'sample') {
          result[key] = '[скрыто в UI]';
        } else if (key === 'markdown') {
          result[key] = '[скрыто в UI: используй таблицу проверки данных]';
        } else {
          result[key] = redactTechnicalPayload(value[key]);
        }
      }
      return result;
    }

    bindFormDirtyHandlers();
    Promise.all([loadProjects(), loadProfiles(), checkLocalStatus()]);
  </script>
</body>
</html>
"""
