// Presentation only: never reload, retry, publish, or modify a draft here.
window.humanizeError = function humanizeError(error) {
  const fallback = 'Не удалось выполнить действие. Обновите страницу. Если ошибка повторяется, обратитесь к администратору.';
  const detail = error && typeof error === 'object'
    ? (error.detail !== undefined ? error.detail : error.message)
    : error;

  if (Array.isArray(detail)) {
    const shareError = detail.some((item) => Array.isArray(item.loc) && item.loc.includes('share'));
    return shareError
      ? 'Проверьте введённые значения. Доля должна быть целым числом от 0 до 100.'
      : 'Проверьте заполнение полей и повторите действие.';
  }
  if (typeof detail !== 'string' || !detail.trim()) return fallback;
  const text = detail.trim();

  if (text === 'Нет связи с приложением. Проверьте, что сервер запущен.') {
    return text;
  }
  if (/simulator|симулятор.*недоступен|connect.*simulator|could not connect to simulator/i.test(text)) {
    return 'Нет связи с симулятором. Проверьте, что симулятор запущен.';
  }
  if (/connecting to keitaro|contacting keitaro/i.test(text)) {
    return 'Нет связи с Keitaro. Проверьте подключение к трекеру.';
  }

  if (/stream revision changed/i.test(text)) {
    return 'Данные потока уже изменились. Обновите страницу и повторите действие.';
  }
  if (/fingerprint|remote stream was modified|changed.*(?:Keitaro|upstream|remotely)|external.*change/i.test(text)) {
    return 'Поток изменился на стороне трекера. Не публикуйте старый черновик поверх новых данных: сначала сравните изменения в редакторе и трекере.';
  }
  if (/read-back|verification.*failed|verification.*mismatch/i.test(text)) {
    return 'Результат публикации не подтверждён. Проверьте состояние потока в трекере, прежде чем отправлять изменения повторно.';
  }
  if (/state[=: ]+['"]?unknown|locked.*state|currently being pushed|cannot cancel.*state/i.test(text)) {
    return 'Состояние синхронизации ещё не подтверждено. Проверьте поток в трекере и дождитесь проверки результата перед дальнейшими изменениями.';
  }
  if (/cannot fetch/i.test(text)) {
    return 'Сначала опубликуйте изменения кнопкой Push или отмените их кнопкой Cancel. Если идёт синхронизация, дождитесь её завершения.';
  }
  if (/last active offer/i.test(text)) return 'Нельзя убрать последний активный оффер из потока.';
  if (/already active/i.test(text)) return 'Этот оффер уже добавлен в поток.';
  if (/sum of pinned weights.*exceeds/i.test(text)) {
    return 'Сумма закреплённых долей превышает 100%. Уменьшите одну из закреплённых долей.';
  }
  if (/(?:all.*pinned|no unpinned)/i.test(text)) {
    return 'Когда все доли закреплены, их сумма должна быть ровно 100%. Исправьте доли или снимите закрепление с одного оффера.';
  }
  if (/pinned weights|share must be an integer|invalid share/i.test(text)) {
    return 'Доля должна быть целым числом от 0 до 100.';
  }
  if (/share.*(?:equal|sum)|sum of shares/i.test(text)) {
    return 'Сумма долей активных офферов должна быть ровно 100%. Проверьте доли и закрепления.';
  }
  if (/no active offers|at least one active offer/i.test(text)) {
    return 'В потоке должен остаться хотя бы один активный оффер.';
  }
  const offer = text.match(/(?:offer|оффер)\s*#?(\d+)/i);
  if (offer && /недоступен|not found|unavailable/i.test(text)) {
    const name = `Оффер #${offer[1]}`;
    return /архиве/i.test(text)
      ? `${name} недоступен в каталоге трекера. Его привязка сохранена в архиве; вернуть её можно, когда оффер снова станет доступен.`
      : `${name} не найден или недоступен. Выберите другой оффер либо проверьте его доступность в трекере.`;
  }
  if (/api.?key|authentication|unauthorized|forbidden|access denied/i.test(text)) {
    return 'Нет доступа к трекеру. Обратитесь к администратору для проверки подключения и прав доступа.';
  }
  if (/does not belong|mismatch campaign/i.test(text)) {
    return 'Данные не соответствуют выбранной кампании. Обновите страницу и выберите кампанию заново.';
  }
  if (/not found/i.test(text)) return 'Кампания, поток или оффер больше недоступны. Обновите страницу и проверьте выбранную кампанию.';
  if (/network|failed to fetch|timeout|timed out|connect|load failed/i.test(text)) {
    return 'Связь с сервером прервалась. Если вы публиковали изменения, проверьте состояние потока перед повторной отправкой.';
  }

  // Keep existing plain Russian copy; never display raw URLs, server responses,
  // validation payloads, stack traces, or unrecognized technical English.
  const remaining = text.replace(/Keitaro|ADROBOT|Fetch Streams|Push to KT|Push to Demo|Push|Cancel|Bring back|Flow\s*\d+|Pin|Unpin/gi, '');
  if (/[А-Яа-яЁё]/.test(text) && !/[a-z]|https?:|[<>{}]|\/api|\/offers|\/streams/i.test(remaining)) return text;
  return fallback;
};
