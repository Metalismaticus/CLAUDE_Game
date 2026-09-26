extends SceneTree
## Строка «типы» docs/TESTING.md: <движок> --headless --path . --script tools/load_all.gd
## Грузит каждый .gd проекта вне res://addons, скрытых папок и папок с .gdignore — движок
## разбирает скрипт под настройками [debug] project.godot и печатает ошибки типов.
## Красное — «с ошибками» не 0 в «Итог:» (код 1), «Parse Error» или «SCRIPT ERROR» в выводе: скрипт
## с ошибкой load() всё равно отдаёт — его выдаёт can_instantiate().
## Сам полностью типизирован: компилируется под теми же настройками. Развёрнут плагином studio.

func _init() -> void:
	var paths: Array[String] = []
	_collect("res://", paths)
	var failed: Array[String] = []
	for path: String in paths:
		var script: Script = load(path) as Script
		if script == null or not script.can_instantiate():
			failed.append(path.trim_prefix("res://"))
	print("Итог: скриптов ", paths.size(), ", с ошибками ", failed.size(), "" if failed.is_empty() else ": " + ", ".join(failed))
	quit(1 if failed.size() > 0 else 0)

func _collect(dir_path: String, paths: Array[String]) -> void:
	if FileAccess.file_exists(dir_path.path_join(".gdignore")):
		return
	for sub: String in DirAccess.get_directories_at(dir_path):
		if sub.begins_with(".") or (dir_path == "res://" and sub == "addons"):
			continue
		_collect(dir_path.path_join(sub), paths)
	for file: String in DirAccess.get_files_at(dir_path):
		if file.get_extension() == "gd":
			paths.append(dir_path.path_join(file))
