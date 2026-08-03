# Handoff и stable state

`handoff_state` хранит текущий status разработки: stable SHA, завершённое, риски, открытые вопросы, next actions и точку продолжения. Это operational документ, а не вечный reference.

На момент создания skill в Gaia нет repo-local canonical handoff-файла; текущий canonical state и безопасный snapshot ведутся отдельным workflow в MemoryHub. Не создавай такой файл только ради полноты карты.

При закрытии stable state проверяются четыре независимых слоя: принятая реализация в Git, согласованность Gaia-документации с implementation/contracts, GitNexus на том же SHA и MemoryHub canonical state/snapshot на том же SHA. Этот skill не пишет в MemoryHub и не выполняет closure самостоятельно.
