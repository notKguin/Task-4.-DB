import os
import uuid
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

from django.conf import settings
from django.core.files.storage import default_storage
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .models import Recipe


XML_DIR = os.path.join(settings.MEDIA_ROOT, 'recipes')
XML_PATH = os.path.join(XML_DIR, 'recipes.xml')


class Storage:
    XML = 'xml'
    DB = 'db'


TEXT_FIELD_TYPES = {'CharField', 'TextField'}


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: dict[str, str]



def ensure_dir() -> None:
    os.makedirs(XML_DIR, exist_ok=True)


def get_recipe_fields():
    """Поля рецепта (без id)."""
    return [f for f in Recipe._meta.fields if f.name != 'id']


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    s = str(value or '').strip().lower()
    return s in {'1', 'true', 'on', 'yes', 'y', 'да'}


def validate_recipe_data(raw: dict[str, Any]) -> ValidationResult:
    """Проверяет корректность значений (для добавления/редактирования в БД)."""
    errors: dict[str, str] = {}

    title = (raw.get('title') or '').strip()
    if not title:
        errors['title'] = 'Название обязательно.'
    elif len(title) > 200:
        errors['title'] = 'Название не должно быть длиннее 200 символов.'

    # Булево поле
    try:
        _ = normalize_bool(raw.get('colories'))
    except Exception:
        errors['colories'] = 'Некорректное значение поля "Диетическое".'

    return ValidationResult(ok=not errors, errors=errors)


def read_from_xml() -> list[dict[str, str]]:
    """Читает рецепты из XML."""
    if not os.path.exists(XML_PATH):
        return []

    tree = ET.parse(XML_PATH)
    root = tree.getroot()
    if root.tag != 'recipes':
        return []

    fields = get_recipe_fields()
    result: list[dict[str, str]] = []

    for el in root.findall('recipe'):
        row: dict[str, str] = {}
        for f in fields:
            node = el.find(f.name)
            row[f.name] = (node.text or '').strip() if node is not None else ''
        result.append(row)

    return result


def write_to_xml(recipes: list[dict[str, Any]]) -> None:
    """Перезаписывает XML рецептов."""
    ensure_dir()
    fields = get_recipe_fields()
    root = ET.Element('recipes')

    for r in recipes:
        recipe_el = ET.SubElement(root, 'recipe')
        for f in fields:
            value = r.get(f.name, '')
            if f.get_internal_type() == 'BooleanField':
                value = '1' if normalize_bool(value) else '0'
            ET.SubElement(recipe_el, f.name).text = str(value or '')

    ET.ElementTree(root).write(XML_PATH, encoding='utf-8', xml_declaration=True)


def import_from_xml(file_path):
    """Импорт рецептов из загруженного XML файла"""
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()

        # Проверяем структуру
        if root.tag != "recipes":
            raise ValueError("Некорректный корневой элемент (ожидался <recipes>).")

        imported = 0
        for idx, el in enumerate(root.findall("recipe"), start=1):
            data = {}
            for field in get_recipe_fields():
                node = el.find(field.name)
                if node is None:
                    raise ValueError(f"Ошибка в рецепте №{idx}: отсутствует тег <{field.name}>.")
                data[field.name] = (node.text or '').strip()

            # Импортируем в XML-хранилище (по умолчанию): добавляем как есть
            recipes = read_from_xml()
            recipes.append(data)
            write_to_xml(recipes)
            imported += 1
        return True, f"Импортировано {imported} рецептов."

    except Exception as e:
        return False, f"Ошибка при импорте: {e}"


def index(request: HttpRequest) -> HttpResponse:
    ensure_dir()

    fields = get_recipe_fields()
    view_from = request.GET.get('view_from', Storage.XML)
    save_to = request.GET.get('save_to', Storage.XML)
    message = ''
    errors: dict[str, str] = {}

    if view_from not in (Storage.XML, Storage.DB):
        view_from = Storage.XML
    if save_to not in (Storage.XML, Storage.DB):
        save_to = Storage.XML

    # ---------- Добавление рецепта ----------
    if request.method == 'POST' and 'add_recipe' in request.POST:
        save_to = request.POST.get('save_to', Storage.XML)
        view_from = request.POST.get('view_from', view_from)

        raw = {f.name: request.POST.get(f.name, '') for f in fields}
        raw['colories'] = normalize_bool(request.POST.get('colories'))

        if save_to == Storage.DB:
            v = validate_recipe_data(raw)
            if not v.ok:
                errors = v.errors
                message = 'Исправьте ошибки в форме.'
            else:
                exists = Recipe.objects.filter(
                    title=raw['title'].strip(),
                    description=raw.get('description', ''),
                    ingredients=raw.get('ingredients', ''),
                    steps=raw.get('steps', ''),
                    colories=raw['colories'],
                ).exists()
                if exists:
                    message = 'Такая запись уже есть в базе. Добавление отменено.'
                else:
                    Recipe.objects.create(**raw)
                    message = 'Сохранено в базе данных.'
        else:
            # XML-хранилище: сохраняем без жёсткой валидации (как раньше), но корректно пишем boolean
            recipes = read_from_xml()
            recipes.append(raw)
            write_to_xml(recipes)
            message = 'Сохранено в XML.'

        # Возвращаемся на страницу с сохранёнными настройками
        return redirect(f"/?view_from={view_from}&save_to={save_to}")

    # ---------- Импорт XML ----------
    if request.method == 'POST' and 'upload_xml' in request.POST:
        uploaded = request.FILES.get('xml_file')
        if not uploaded:
            message = 'Файл не выбран.'
        else:
            safe_name = f"upload_{uuid.uuid4().hex}.xml"
            upload_path = os.path.join(XML_DIR, safe_name)
            ensure_dir()
            with default_storage.open(upload_path, 'wb+') as destination:
                for chunk in uploaded.chunks():
                    destination.write(chunk)

            ok, msg = import_from_xml(upload_path)
            os.remove(upload_path)
            message = msg

    # ---------- Данные для вывода ----------
    recipes: list[dict[str, Any]]
    if view_from == Storage.DB:
        q = (request.GET.get('q') or '').strip()

        qs = Recipe.objects.all().order_by('-id')
        if q:
            or_q = Q()
            for f in fields:
                if f.get_internal_type() in TEXT_FIELD_TYPES:
                    or_q |= Q(**{f"{f.name}__icontains": q})
            qs = qs.filter(or_q)

        recipes = [
            {'id': r.id, **{f.name: getattr(r, f.name, '') for f in fields}}
            for r in qs
        ]
    else:
        recipes = read_from_xml()

    xml_exists = os.path.exists(XML_PATH)

    return render(request, 'recipes/index.html', {
        'fields': fields,
        'recipes': recipes,
        'xml_exists': xml_exists,
        'xml_path': XML_PATH.replace(settings.MEDIA_ROOT, settings.MEDIA_URL),
        'message': message,
        'errors': errors,
        'view_from': view_from,
        'save_to': save_to,
    })


def _recipe_to_initial(recipe: Recipe) -> dict[str, Any]:
    fields = get_recipe_fields()
    return {f.name: getattr(recipe, f.name, '') for f in fields}


def db_edit(request: HttpRequest, pk: int) -> HttpResponse:
    recipe = get_object_or_404(Recipe, pk=pk)
    fields = get_recipe_fields()
    message = ''
    errors: dict[str, str] = {}

    if request.method == 'POST':
        raw = {f.name: request.POST.get(f.name, '') for f in fields}
        raw['colories'] = normalize_bool(request.POST.get('colories'))

        v = validate_recipe_data(raw)
        if not v.ok:
            errors = v.errors
            message = 'Исправьте ошибки в форме.'
        else:
            # Проверка дубликата (кроме текущей записи)
            exists = Recipe.objects.filter(
                title=raw['title'].strip(),
                description=raw.get('description', ''),
                ingredients=raw.get('ingredients', ''),
                steps=raw.get('steps', ''),
                colories=raw['colories'],
            ).exclude(pk=recipe.pk).exists()

            if exists:
                message = 'Такая запись уже есть в базе. Сохранение отменено.'
            else:
                for k, v_ in raw.items():
                    setattr(recipe, k, v_)
                recipe.save(update_fields=list(raw.keys()))
                return redirect('/?view_from=db&save_to=db')

        initial = raw
    else:
        initial = _recipe_to_initial(recipe)

    return render(request, 'recipes/edit.html', {
        'fields': fields,
        'recipe_id': recipe.id,
        'initial': initial,
        'message': message,
        'errors': errors,
    })


@require_POST
def db_delete(request: HttpRequest, pk: int) -> HttpResponse:
    recipe = get_object_or_404(Recipe, pk=pk)
    recipe.delete()
    return redirect('/?view_from=db&save_to=db')


@require_GET
def ajax_db_search(request: HttpRequest) -> HttpResponse:
    """AJAX-поиск по данным БД. Возвращает HTML (не JSON)."""
    q = (request.GET.get('q') or '').strip()
    fields = get_recipe_fields()

    qs = Recipe.objects.all().order_by('-id')
    if q:
        or_q = Q()
        for f in fields:
            if f.get_internal_type() in TEXT_FIELD_TYPES:
                or_q |= Q(**{f"{f.name}__icontains": q})
        qs = qs.filter(or_q)

    recipes = [
        {'id': r.id, **{f.name: getattr(r, f.name, '') for f in fields}}
        for r in qs
    ]

    return render(request, 'recipes/_rows.html', {
        'fields': fields,
        'recipes': recipes,
        'view_from': Storage.DB,
    })
