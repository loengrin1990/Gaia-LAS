"""Cross-platform desktop window for the independent Veil copy."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QHBoxLayout,
    QLabel, QMainWindow, QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget)

from veil_core.extraction import ExtractionError, read_material
from veil_core.masking import MaskResult, mask_with_review
from veil_core.model_review import ModelReview, review_residual_pii, stop_model

CATEGORY_LABELS = {
    "PERSON": "Имена", "EMAIL": "Электронная почта", "PHONE": "Телефоны",
    "ADDRESS": "Адреса", "PASSPORT": "Документы", "INN": "ИНН",
    "ID": "Идентификаторы", "CONTRACT": "Договоры", "REQUEST": "Обращения",
    "INVOICE": "Счета", "LINK": "Ссылки и пути", "CLIENT": "Организации",
    "VENDOR": "Поставщики", "AMOUNT": "Суммы", "BANK": "Банковские данные",
    "INTERNAL_ID": "Внутренние номера", "OTHER": "Другое",
}


class ReviewWorker(QThread):
    completed = Signal(object, object)
    failed = Signal()

    def __init__(self, text: str, use_model: bool) -> None:
        super().__init__()
        self.text, self.use_model = text, use_model

    def run(self) -> None:
        try:
            result = mask_with_review("материал", self.text, include_llm_review=False)
            model = review_residual_pii(result.masked_text) if self.use_model else None
            self.completed.emit(result, model)
        except Exception:
            self.failed.emit()


class StopWorker(QThread):
    completed = Signal(bool)

    def run(self) -> None:
        self.completed.emit(stop_model())


class VeilWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Вуаль — локальная очистка текста")
        self.resize(1100, 760)
        self.source_path: Path | None = None
        self.result: MaskResult | None = None
        self.source_at_review = ""
        self.worker: ReviewWorker | None = None
        self.stop_worker: StopWorker | None = None
        body = QWidget()
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)
        title = QLabel("Вуаль")
        title.setStyleSheet("font-size: 24px; font-weight: bold")
        layout.addWidget(title)
        layout.addWidget(QLabel("Локальная очистка текста от персональных данных. Перед использованием проверьте результат вручную."))

        actions = QHBoxLayout()
        layout.addLayout(actions)
        open_button = QPushButton("Открыть файл")
        open_button.clicked.connect(self.open_file)
        actions.addWidget(open_button)
        self.run_button = QPushButton("Очистить и проверить")
        self.run_button.clicked.connect(self.start_review)
        actions.addWidget(self.run_button)
        self.model_enabled = QCheckBox("Дополнительно проверить локальной моделью")
        self.model_enabled.setChecked(True)
        actions.addWidget(self.model_enabled)
        actions.addStretch()
        stop_button = QPushButton("Остановить модель")
        stop_button.clicked.connect(self.unload_model)
        actions.addWidget(stop_button)
        self.file_note = QLabel("Поддерживаются TXT, MD, PDF и DOCX. Исходный файл не изменяется.")
        layout.addWidget(self.file_note)

        columns = QHBoxLayout()
        layout.addLayout(columns, stretch=1)
        left, right = QVBoxLayout(), QVBoxLayout()
        columns.addLayout(left, stretch=1)
        columns.addLayout(right, stretch=1)
        left.addWidget(QLabel("Исходный текст"))
        self.input = QTextEdit()
        left.addWidget(self.input)
        right.addWidget(QLabel("Очищенный текст"))
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        right.addWidget(self.output)
        self.status = QLabel("Добавьте текст или откройте файл.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.reviewed = QCheckBox("Я просмотрел очищенный текст и готов использовать копию")
        layout.addWidget(self.reviewed)
        bottom = QHBoxLayout()
        layout.addLayout(bottom)
        copy_button = QPushButton("Скопировать очищенный текст")
        copy_button.clicked.connect(self.copy_result)
        bottom.addWidget(copy_button)
        save_button = QPushButton("Сохранить очищенную копию")
        save_button.clicked.connect(self.save_result)
        bottom.addWidget(save_button)
        bottom.addStretch()
        clear_button = QPushButton("Очистить окно")
        clear_button.clicked.connect(self.clear)
        bottom.addWidget(clear_button)

    def open_file(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Открыть материал", "", "Материалы (*.txt *.md *.pdf *.docx);;Все файлы (*)")
        if not selected:
            return
        path = Path(selected)
        try:
            text, note = read_material(path)
        except ExtractionError as exc:
            QMessageBox.warning(self, "Не удалось открыть материал", str(exc))
            return
        except OSError:
            QMessageBox.warning(self, "Не удалось открыть материал", "Проверьте доступ к файлу и повторите попытку.")
            return
        self.source_path = path
        self.input.setPlainText(text)
        self.result = None
        self.reviewed.setChecked(False)
        self.output.clear()
        self.file_note.setText(note)
        self.status.setText("Текст извлечён. Проверьте его полноту, затем нажмите «Очистить и проверить».")

    def start_review(self) -> None:
        text = self.input.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "Нет текста", "Добавьте текст для очистки.")
            return
        if self.worker and self.worker.isRunning():
            return
        self.run_button.setEnabled(False)
        self.result = None
        self.reviewed.setChecked(False)
        self.output.clear()
        self.status.setText("Идёт локальная очистка и проверка…")
        self.worker = ReviewWorker(text, self.model_enabled.isChecked())
        self.worker.completed.connect(lambda result, model: self._finish(text, result, model))
        self.worker.failed.connect(self._fail)
        self.worker.start()

    def _finish(self, text: str, result: MaskResult, model: ModelReview | None) -> None:
        self.run_button.setEnabled(True)
        self.source_at_review, self.result = text, result
        self.output.setPlainText(result.masked_text)
        counts = ", ".join(f"{CATEGORY_LABELS.get(category, 'Другие данные')}: {count}"
                           for category, count in sorted(result.review.counts.items())) or "замен нет"
        messages = [f"Замен: {result.review.total_replacements}. {counts}."]
        if result.review.unresolved_pii:
            messages.append("По правилам остаётся возможный риск. Нужна ручная проверка.")
        messages.append(model.message if model else "Дополнительная проверка моделью не запускалась.")
        messages.append("Проверьте полноту извлечённого текста и все оставшиеся данные перед использованием.")
        self.status.setText(" ".join(messages))

    def _fail(self) -> None:
        self.run_button.setEnabled(True)
        self.status.setText("Очистка не завершена. Результат не создан.")
        QMessageBox.warning(self, "Не удалось очистить текст", "Очистка не завершена. Проверьте материал и повторите попытку.")

    def _ready_text(self) -> str | None:
        if not self.run_button.isEnabled() or self.result is None or self.input.toPlainText() != self.source_at_review:
            QMessageBox.warning(self, "Нужна повторная проверка", "Исходный текст изменился или ещё не проверен. Запустите очистку заново.")
            return None
        if not self.reviewed.isChecked():
            QMessageBox.warning(self, "Просмотрите результат", "Просмотрите очищенный текст и отметьте подтверждение под ним.")
            return None
        return self.result.masked_text

    def copy_result(self) -> None:
        text = self._ready_text()
        if text is not None:
            QApplication.clipboard().setText(text)
            self.status.setText("Очищенный текст скопирован. Буфер обмена может быть доступен другим приложениям.")

    def save_result(self) -> None:
        text = self._ready_text()
        if text is None:
            return
        name = (self.source_path.stem + "-очищено.txt") if self.source_path else "очищенный-текст.txt"
        selected, _ = QFileDialog.getSaveFileName(self, "Сохранить очищенную копию", name, "Текст UTF-8 (*.txt)")
        if not selected:
            return
        target = Path(selected)
        if self.source_path and target.resolve() == self.source_path.resolve():
            QMessageBox.warning(self, "Исходный файл защищён", "Выберите другое имя. Исходный файл нельзя перезаписать.")
            return
        try:
            target.write_text(text, encoding="utf-8")
        except OSError:
            QMessageBox.warning(self, "Не удалось сохранить", "Проверьте доступ к выбранной папке.")
            return
        self.status.setText("Очищенная копия сохранена. Исходный файл не менялся.")

    def unload_model(self) -> None:
        if self.stop_worker and self.stop_worker.isRunning():
            return
        self.status.setText("Освобождаю память локальной модели…")
        self.stop_worker = StopWorker()
        self.stop_worker.completed.connect(lambda ok: self.status.setText(
            "Модель остановлена. Программа локальных моделей продолжает работать." if ok
            else "Не удалось остановить модель. Проверьте, запущена ли программа локальных моделей."))
        self.stop_worker.start()

    def clear(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        self.input.clear()
        self.output.clear()
        self.result = None
        self.source_at_review = ""
        self.source_path = None
        self.reviewed.setChecked(False)
        self.file_note.setText("Поддерживаются TXT, MD, PDF и DOCX. Исходный файл не изменяется.")
        self.status.setText("Окно очищено.")


def main() -> None:
    app = QApplication(sys.argv)
    window = VeilWindow()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
