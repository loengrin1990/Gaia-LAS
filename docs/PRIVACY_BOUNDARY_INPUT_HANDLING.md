# PB-0: граница приватности и обработки входов

Этот нормативный контракт определяет последнюю privacy-границу перед будущей OC-5 Runtime Integration. PB-0 не вызывает внешние модели, не выбирает runtime и не меняет OC-5 routing. Он только типизированно фиксирует, может ли внешний маршрут быть рассмотрен позднее.

## Состояния обработки

| Состояние | Обработка внутри Gaia | Возможность внешнего раскрытия |
|---|---|---|
| `standard` | Разрешена | Рассматривается только при origin-validated typed evidence. |
| `restricted` | Разрешена только локально | Запрещена. |
| `unknown` | Разрешена только локально | Запрещена до контролируемого разрешения. |

`unknown` — внутреннее fail-closed состояние. Пользователь не выбирает его вручную для каждого сообщения и Gaia не определяет `standard` по виду текста или мнению языковой модели.

## Источники и происхождение

- Operational Context сохраняет собственные `standard` / `restricted` semantics. Подтверждённый standard OC input получает PB-0 evidence от безопасной confirmation/provenance-связи; отсутствующая или повреждённая связь даёт `unknown`.
- Выбранный Memory unit обязан нести handling. Legacy/pre-PB Memory без надёжной классификации — `unknown`.
- Новый substantive free-form пользовательский текст — `unknown`.
- Session unit наследует наиболее строгую обработку каждого материала, из которого он создан. Если его происхождение не доказано, он — `unknown`.
- Trusted system-owned control text без user/project semantic payload может быть `standard` только через закрытый registry: boundary сверяет control identifier и точный system-owned template. Произвольный текст с вручную собранным evidence не становится standard.
- Document/extracted content наследует обработку reviewed source; неизвестный source не становится standard автоматически.

## Производные материалы, конфликт и бюджет

Производный результат сохраняет наиболее строгую обработку каждого material dependency. Это относится к summary, decision, suppression/invalidation и conflict information. Нельзя понизить handling, исключив unit из сериализованного package из-за budget: весь требуемый исходный набор всё равно участвует в aggregate handling и eligibility.

## Eligibility внешнего маршрута

PB-0 разделяет caller-provided source input и runtime-attested `ValidatedPrivacyInput`. Final eligibility принимает только второй тип и проверяет его PB runtime-attestation; самостоятельный `HandledInput`, вручную созданный validated-looking object или copied evidence fail closed. `standard` получает attestation только после origin-specific validation: OC — через controlled confirmation/provenance, Memory — через reviewed typed evidence, system control — через identifier и точное совпадение canonical template.

PB-0 возвращает `eligible_for_external=true` только если все required contributing inputs являются runtime-attested valid standard inputs. При любом `restricted`, `unknown`, нарушенном evidence или неаттестованном caller input возвращается единственное неразличающее disclosure-facing состояние `local_processing_required`. Оно не сообщает причину, число, тип или provenance скрытого input. Детальная причина PB-0 не сериализуется в package и не передаётся downstream; v0 её не формирует.

Неразрешённая ambiguity из одних valid standard inputs сохраняется явной и не получает automatic winner/current truth. Сама по себе она не делает внешний маршрут недоступным: поздний consumer обязан сохранить ambiguity, а не выдавать один вариант как current truth.

External call не входит в PB-0. Поздний OC-5 обязан использовать этот результат как fail-closed input, а не как самостоятельное разрешение раскрытия.
