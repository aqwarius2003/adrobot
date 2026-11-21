document.addEventListener('DOMContentLoaded', () => {
  const campaignSelect = document.getElementById('campaignSelect');
  const campaignSearch = document.getElementById('campaignSearch');
  const campaignPicker = new window.CampaignPicker({
    select: campaignSelect,
    input: campaignSearch,
    dropdown: document.getElementById('campaignDropdown'),
  });
  const fetchStreamsBtn = document.getElementById('fetchStreamsBtn');
  const fetchSpinner = document.getElementById('fetchSpinner');
  const lastSyncTime = document.getElementById('lastSyncTime');
  const streamsContainer = document.getElementById('streamsContainer');
  const editorNotice = document.getElementById('editorNotice');
  const viewInKtLink = document.getElementById('viewInKtLink');

  let currentCampaignId = null;
  let currentCampaignData = null;

  const apiPrefix = (window.APP_CONFIG && window.APP_CONFIG.api_prefix) || '/api';
  const currentMode = (window.APP_CONFIG && window.APP_CONFIG.current_mode) || 'real';
  const safeFetch =
    (window.apiClient && window.apiClient.fetch) ||
    window.apiFetch ||
    (typeof window.fetch === 'function' ? window.fetch.bind(window) : fetch);

  // Check URL query parameters
  const urlParams = new URLSearchParams(window.location.search);
  const paramCampaignId = urlParams.get('campaign_id');
  // Load campaigns list
  async function loadCampaigns() {
    try {
      const res = await safeFetch(`${apiPrefix}/campaigns`);
      if (!res.ok) throw new Error('Failed to load campaigns');
      const campaigns = await res.json();

      // Separate campaign selection per mode
      const savedCampaignId = localStorage.getItem(`adrobot_selected_campaign_${currentMode}`);
      const effectiveId = paramCampaignId
        ? parseInt(paramCampaignId, 10)
        : savedCampaignId
        ? parseInt(savedCampaignId, 10)
        : null;

      const targetAutoId = effectiveId && campaigns.some((c) => c.remote_campaign_id === effectiveId)
        ? effectiveId
        : (window.APP_CONFIG && window.APP_CONFIG.is_demo && campaigns.length > 0)
        ? campaigns[0].remote_campaign_id
        : null;

      let optionsHtml = '<option value="">-- Выберите кампанию --</option>';
      campaigns.forEach((c) => {
        const isSelected = targetAutoId && targetAutoId === c.remote_campaign_id;
        const selectedAttr = isSelected ? 'selected' : '';
        optionsHtml += `<option value="${c.remote_campaign_id}" ${selectedAttr}>[ID: ${c.remote_campaign_id}] ${escapeHtml(c.name)}</option>`;
      });
      campaignSelect.innerHTML = optionsHtml;
      campaignPicker.sync();
      campaignSearch.disabled = false;

      // Synchronize context and load campaign data
      if (targetAutoId) {
        currentCampaignId = targetAutoId;
        campaignSelect.value = String(currentCampaignId);
        fetchStreamsBtn.disabled = false;
        fetchStreamsBtn.removeAttribute('title');
        localStorage.setItem(`adrobot_selected_campaign_${currentMode}`, currentCampaignId);
        updateViewInKtLink(currentCampaignId);
        await loadCampaignData(currentCampaignId);
      } else {
        updateViewInKtLink(null);
        fetchStreamsBtn.disabled = true;
        fetchStreamsBtn.title = 'Выберите кампанию из списка для загрузки потоков';
      }
    } catch (err) {
      console.warn('Could not load campaigns:', err);
      if (err && err.isAppUnreachable) {
        showAppUnreachableNotice();
      }
      showToast(err.message, 'error');
    }
  }
  loadCampaigns();

  // Load local draft / baseline for selected campaign without upstream fetch
  async function loadCampaignData(campaignId) {
    clearNotice();
    updateViewInKtLink(campaignId);

    try {
      const res = await safeFetch(`${apiPrefix}/campaigns/${campaignId}`);
      if (res.status === 404) {
        // Campaign exists in KT list, but not imported locally yet
        currentCampaignData = null;
        renderUnimportedState();
        if (lastSyncTime) lastSyncTime.textContent = 'Не синхронизировано';
        return;
      }

      if (!res.ok) {
        throw new Error(`Ошибка загрузки данных кампании: ${res.statusText}`);
      }

      const data = await res.json();
      if (currentCampaignId && data.remote_campaign_id !== currentCampaignId) {
        console.error('Mismatch campaign ID', data.remote_campaign_id, currentCampaignId);
        showToast('Ошибка: полученные данные не соответствуют выбранной кампании', 'error');
        return;
      }
      currentCampaignData = data;
      renderCampaignStreams(data);
      updateSyncTimeDisplay(data.last_sync_at);
      fetchStreamStats(campaignId);
    } catch (err) {
      console.warn('Error loading local campaign state:', err);
      if (err && err.isAppUnreachable) {
        showAppUnreachableNotice();
      }
      showToast(err.message, 'error');
    }
  }

  function renderUnimportedState() {
    streamsContainer.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">&#8645;</div>
        <h3>Кампания не импортирована</h3>
        <p>Локального снимка потоков ещё нет. Нажмите <strong>Fetch Streams</strong> для первичного импорта из трекера.</p>
      </div>`;
  }

  function renderEmptyState() {
    streamsContainer.innerHTML = `
      <div class="empty-state" id="emptyStateBox">
        <div class="empty-icon">&#8645;</div>
        <h3>Выберите кампанию</h3>
        <p>Выберите кампанию из списка выше для просмотра потоков и редактирования весов.</p>
      </div>`;
  }

  function updateViewInKtLink(remoteCampaignId) {
    if (!viewInKtLink) return;
    const isDemo = window.APP_CONFIG && window.APP_CONFIG.is_demo;
    const ktUrl = window.APP_CONFIG && window.APP_CONFIG.keitaro_base_url;
    if (remoteCampaignId && ktUrl && !isDemo) {
      const cleanBase = ktUrl.replace(/\/+$/, '');
      viewInKtLink.href = `${cleanBase}/admin/#!/campaign/${remoteCampaignId}/main`;
      viewInKtLink.target = '_blank';
      viewInKtLink.rel = 'noopener noreferrer';
      viewInKtLink.classList.remove('hidden');
    } else {
      viewInKtLink.removeAttribute('href');
      viewInKtLink.classList.add('hidden');
    }
  }

  function updateSyncTimeDisplay(isoString) {
    if (!lastSyncTime) return;
    if (!isoString) {
      lastSyncTime.textContent = 'Не синхронизировано';
      return;
    }
    const d = new Date(isoString);
    lastSyncTime.textContent = isNaN(d.getTime()) ? isoString : d.toLocaleTimeString();
  }

  campaignSelect.addEventListener('change', async () => {
    const val = campaignSelect.value;
    if (val) {
      currentCampaignId = parseInt(val, 10);
      updateViewInKtLink(currentCampaignId);
      fetchStreamsBtn.disabled = false;
      fetchStreamsBtn.removeAttribute('title');
      const newUrl = new URL(window.location);
      newUrl.searchParams.set('campaign_id', currentCampaignId);
      window.history.replaceState({}, '', newUrl);
      localStorage.setItem(`adrobot_selected_campaign_${currentMode}`, currentCampaignId);
      await loadCampaignData(currentCampaignId);
    } else {
      currentCampaignId = null;
      currentCampaignData = null;
      fetchStreamsBtn.disabled = true;
      fetchStreamsBtn.title = 'Выберите кампанию из списка для загрузки потоков';
      const newUrl = new URL(window.location);
      newUrl.searchParams.delete('campaign_id');
      window.history.replaceState({}, '', newUrl);
      localStorage.removeItem(`adrobot_selected_campaign_${currentMode}`);
      renderEmptyState();
      clearNotice();
      updateViewInKtLink(null);
      if (lastSyncTime) lastSyncTime.textContent = 'Не выбрана';
    }
  });

  fetchStreamsBtn.addEventListener('click', () => {
    if (currentCampaignId) {
      fetchStreams(currentCampaignId);
    }
  });

  async function fetchStreams(campaignId) {
    fetchStreamsBtn.disabled = true;
    fetchSpinner.classList.remove('hidden');
    clearNotice();

    try {
      const res = await safeFetch(`${apiPrefix}/campaigns/${campaignId}/fetch-streams`, {
        method: 'POST',
      });

      const data = await res.json();
      if (!res.ok) {
        if (res.status === 409) {
          showNotice(
            data.detail ||
              'Невозможно выполнить Fetch: имеются неопубликованные изменения. Нажмите Push или Cancel.',
            'conflict'
          );
        }
        throw new Error(data.detail || 'Ошибка при загрузке потоков из трекера');
      }

      if (currentCampaignId && data.remote_campaign_id !== currentCampaignId) {
        console.error('Mismatch campaign ID', data.remote_campaign_id, currentCampaignId);
        showToast('Ошибка: полученные данные не соответствуют выбранной кампании', 'error');
        return;
      }

      currentCampaignData = data;
      renderCampaignStreams(data);
      updateSyncTimeDisplay(data.last_sync_at || new Date().toISOString());
      fetchStreamStats(campaignId);

      const isDemo = window.APP_CONFIG && window.APP_CONFIG.is_demo;
      showToast(
        isDemo ? 'Потоки загружены из симулятора' : 'Потоки успешно синхронизированы с Keitaro',
        'success'
      );
    } catch (err) {
      if (err && err.isAppUnreachable) {
        showAppUnreachableNotice();
      }
      showToast(err.message, 'error');
    } finally {
      fetchStreamsBtn.disabled = false;
      fetchSpinner.classList.add('hidden');
    }
  }

  function showNotice(msg, type = 'info', actionBtn = null) {
    editorNotice.className = `editor-notice notice-${type}`;
    editorNotice.textContent = ['conflict', 'error', 'warning'].includes(type) ? window.humanizeError(msg) : msg;
    if (actionBtn) {
      editorNotice.appendChild(actionBtn);
    }
    editorNotice.classList.remove('hidden');
  }

  function showAppUnreachableNotice() {
    const reconnectBtn = document.createElement('button');
    reconnectBtn.className = 'btn btn-secondary btn-sm';
    reconnectBtn.style.marginLeft = '12px';
    reconnectBtn.textContent = 'Повторить попытку';
    reconnectBtn.addEventListener('click', async () => {
      clearNotice();
      if (currentCampaignId) {
        await loadCampaignData(currentCampaignId);
      } else {
        await loadCampaigns();
      }
    });
    showNotice(
      'Нет связи с приложением. Проверьте, что сервер запущен.',
      'error',
      reconnectBtn
    );
  }

  function clearNotice() {
    editorNotice.classList.add('hidden');
    editorNotice.textContent = '';
  }

  function renderCampaignStreams(campaign) {
    streamsContainer.querySelectorAll('.stream-card').forEach((c) => {
      if (c._combobox && typeof c._combobox.destroy === 'function') c._combobox.destroy();
    });
    streamsContainer.innerHTML = '';

    if (!campaign.streams || campaign.streams.length === 0) {
      streamsContainer.innerHTML = `
        <div class="empty-state">
          <h3>В кампании нет потоков</h3>
          <p>В трекере не найдено потоков для этой кампании.</p>
        </div>`;
      return;
    }

    const managedStreams = campaign.streams.filter((s) => s.is_managed);
    const unmanagedStreams = campaign.streams.filter((s) => !s.is_managed);

    // Render managed streams cards first
    managedStreams.forEach((stream) => {
      const card = createStreamCard(stream);
      streamsContainer.appendChild(card);
    });

    // Render unmanaged (opaque) streams after managed streams as compact single-line rows
    unmanagedStreams.forEach((stream) => {
      const row = createUnmanagedStreamRow(stream);
      streamsContainer.appendChild(row);
    });
  }

  let currentStatsSeq = 0;

  async function fetchStreamStats(campaignId) {
    const seq = ++currentStatsSeq;
    try {
      const res = await safeFetch(`${apiPrefix}/campaigns/${campaignId}/stream-stats`);
      if (!res.ok) return;
      const data = await res.json();
      // Protect against out-of-order responses when switching campaigns or mode
      if (seq !== currentStatsSeq || campaignId !== currentCampaignId) return;

      if (data.status === 'ok') {
        const clicksMap = data.clicks_by_stream || {};
        document.querySelectorAll('.stream-clicks[id^="stream-clicks-"]').forEach((el) => {
          const streamId = parseInt(el.id.replace('stream-clicks-', ''), 10);
          if (!isNaN(streamId)) {
            const clicks = clicksMap[streamId] !== undefined ? clicksMap[streamId] : 0;
            el.textContent = String(clicks);
          }
        });
      }
    } catch (err) {
      console.warn('Failed to load stream clicks stats:', err);
    }
  }

  // Compact row for streams without editable offers (Flow 1 / redirect / opaque)
  // Neutral display label only; preserve the upstream stream name in data.
  function createUnmanagedStreamRow(stream) {
    const row = document.createElement('div');
    row.className = 'unmanaged-stream-row';
    row.id = `stream-row-${stream.id}`;

    const title = document.createElement('span');
    title.className = 'unmanaged-stream-title';
    title.textContent = `Stream: Flow ${stream.position} (#${stream.position})`;

    const meta = document.createElement('span');
    meta.className = 'unmanaged-stream-meta';
    meta.innerHTML = ` filter by: None, stream_id: ${escapeHtml(String(stream.remote_stream_id))}, clicks today: <span class="stream-clicks" id="stream-clicks-${escapeHtml(String(stream.remote_stream_id))}">—</span>`;

    row.appendChild(title);
    row.appendChild(meta);
    return row;
  }

  function getWeightEditLimits(stream, offer) {
    const active = stream.offers.filter((item) => item.state === 'active');
    const others = active.filter((item) => item.offer_id !== offer.offer_id);
    const pinnedShare = others.filter((item) => item.is_pinned).reduce((sum, item) => sum + item.share, 0);
    const max = Math.max(0, 100 - pinnedShare);
    const hasRecipient = others.some((item) => !item.is_pinned);
    const min = hasRecipient ? 0 : max;
    const locked = ['publishing', 'verifying', 'unknown'].includes(stream.sync_status);
    let reason = '';
    if (locked) reason = 'Дождитесь завершения синхронизации потока.';
    else if (active.length === 1) reason = 'Единственный активный оффер получает 100%. Добавьте ещё один оффер, чтобы менять доли.';
    else if (min === max) reason = 'Некуда перераспределить долю. Снимите закрепление с другого оффера, чтобы менять проценты.';
    return { min, max, disabled: locked || min === max, reason };
  }

  function createStreamCard(stream) {
    const card = document.createElement('div');
    const isDirty = stream.sync_status === 'dirty';
    card.className = `stream-card status-${stream.sync_status}`;
    card.id = `stream-card-${stream.id}`;

    // Header
    const header = document.createElement('div');
    header.className = 'stream-card-header';

    const headerInfo = document.createElement('div');
    headerInfo.className = 'stream-header-info';

    const title = document.createElement('span');
    title.className = 'stream-title';
    title.textContent = stream.name;

    const idBadge = document.createElement('span');
    idBadge.className = 'stream-id-badge';
    idBadge.textContent = `Stream ID: ${stream.remote_stream_id}`;

    const statusPill = document.createElement('span');
    statusPill.className = `stream-status-pill pill-${stream.sync_status}`;
    statusPill.textContent = getStatusText(stream.sync_status, stream.is_managed);

    headerInfo.appendChild(title);
    headerInfo.appendChild(idBadge);
    headerInfo.appendChild(statusPill);

    // Actions
    const actions = document.createElement('div');
    actions.className = 'stream-actions';

    if (stream.is_managed) {
      const isDemo = window.APP_CONFIG && window.APP_CONFIG.is_demo;
      const pushBtn = document.createElement('button');
      pushBtn.className = 'btn btn-primary';
      pushBtn.textContent = isDemo ? 'Push to Demo' : 'Push to KT';
      if (isDemo) {
        pushBtn.setAttribute(
          'data-tooltip',
          'Опубликовать активные офферы в симулятор'
        );
      } else {
        pushBtn.setAttribute('data-tooltip', 'Опубликовать активные офферы в Keitaro');
      }
      if (stream.sync_status === 'unknown') {
        const reconcileBtn = document.createElement('button');
        reconcileBtn.className = 'btn btn-primary';
        reconcileBtn.textContent = 'Проверить результат';
        reconcileBtn.setAttribute(
          'data-tooltip',
          isDemo
            ? 'Проверить фактическое состояние потока в симуляторе без отправки новых данных'
            : 'Проверить фактическое состояние потока в Keitaro без отправки новых данных'
        );
        reconcileBtn.addEventListener('click', () => reconcileStream(stream));
        actions.appendChild(reconcileBtn);
      }

      pushBtn.disabled = !isDirty || ['publishing', 'verifying', 'unknown', 'conflict'].includes(stream.sync_status);
      pushBtn.addEventListener('click', () => pushStream(stream));

      const cancelBtn = document.createElement('button');
      cancelBtn.className = 'btn btn-secondary';
      cancelBtn.textContent = 'Cancel';
      // Cancel is enabled when stream is dirty OR in conflict
      cancelBtn.disabled = (!isDirty && stream.sync_status !== 'conflict') || stream.sync_status === 'publishing';
      cancelBtn.setAttribute('data-tooltip', 'Сбросить черновик до последнего синхронизированного состояния');
      cancelBtn.addEventListener('click', () => cancelStream(stream));

      if (stream.sync_status !== 'unknown') {
        actions.appendChild(pushBtn);
      }
      actions.appendChild(cancelBtn);
    }

    header.appendChild(headerInfo);
    header.appendChild(actions);
    card.appendChild(header);

    // Managed Table
    const tableWrapper = document.createElement('div');
    tableWrapper.className = 'offers-table-wrapper';

    const table = document.createElement('table');
    table.className = 'offers-table';
    table.innerHTML = `
      <thead>
        <tr>
          <th>Оффер</th>
          <th style="width: 100px;">Доля</th>
          <th style="width: 110px;">Pin</th>
          <th style="width: 120px;">Состояние</th>
          <th style="width: 130px; text-align: right;">Действие</th>
        </tr>
      </thead>
      <tbody></tbody>`;

    const tbody = table.querySelector('tbody');
    let totalActiveShare = 0;
    let activeCount = 0;

    stream.offers.forEach((off) => {
      const tr = document.createElement('tr');
      const isRemoved = off.state === 'removed';
      tr.className = `offer-row ${isRemoved ? 'row-removed' : ''}`;

      if (!isRemoved) {
        totalActiveShare += off.share;
        activeCount++;
      }

      // Offer Name & ID
      const tdName = document.createElement('td');
      tdName.className = 'col-offer';
      const nameDiv = document.createElement('div');
      nameDiv.className = 'offer-name-cell';
      nameDiv.textContent = off.name || `Оффер #${off.offer_id}`;
      const idDiv = document.createElement('div');
      idDiv.className = 'offer-id-sub';
      idDiv.textContent = `Offer ID: ${off.offer_id}`;
      tdName.appendChild(nameDiv);
      tdName.appendChild(idDiv);

      // Share Input
      const tdShare = document.createElement('td');
      tdShare.dataset.label = 'Доля';
      if (isRemoved) {
        tdShare.innerHTML = '<span>0%</span>';
      } else {
        const wrapper = document.createElement('div');
        wrapper.className = 'weight-input-wrapper';
        const input = document.createElement('input');
        input.type = 'number';
        input.setAttribute(
          'aria-label',
          `Доля оффера ${off.name || off.offer_id}, поток Flow ${stream.position || ''}`
        );
        const limits = getWeightEditLimits(stream, off);
        input.min = String(limits.min);
        input.max = String(limits.max);
        input.step = '1';
        input.disabled = limits.disabled;
        input.title = limits.reason;
        wrapper.title = limits.reason;
        if (!input.dataset) input.dataset = {};
        input.dataset.offerId = String(off.offer_id);
        input.dataset.controlRole = 'weight-input';
        input.setAttribute('data-offer-id', String(off.offer_id));
        input.setAttribute('data-control-role', 'weight-input');
        input.className = 'form-control weight-input';
        input.value = off.share;
        input.addEventListener('change', async () => {
          const newShare = Number(input.value);
          if (input.disabled || !String(input.value).trim() || !Number.isInteger(newShare) || newShare < limits.min || newShare > limits.max) {
            input.value = off.share;
            return;
          }
          if (newShare === off.share) return;
          input.disabled = true;
          try {
            await updateWeight(stream, off.offer_id, newShare, null);
          } finally {
            // A successful response replaces the card; on failure restore this input.
            input.value = off.share;
            input.disabled = limits.disabled;
          }
        });
        wrapper.appendChild(input);
        wrapper.appendChild(document.createTextNode('%'));
        tdShare.appendChild(wrapper);
      }

      // Pin Toggle Button
      const tdPin = document.createElement('td');
      tdPin.className = 'col-pin';
      tdPin.dataset.label = 'Pin';
      const pinBtn = document.createElement('button');
      pinBtn.type = 'button';
      pinBtn.className = 'btn-pin';
      pinBtn.disabled = isRemoved;
      if (!pinBtn.dataset) pinBtn.dataset = {};
      pinBtn.dataset.offerId = String(off.offer_id);
      pinBtn.dataset.controlRole = 'pin-btn';
      pinBtn.setAttribute('data-offer-id', String(off.offer_id));
      pinBtn.setAttribute('data-control-role', 'pin-btn');
      pinBtn.setAttribute('aria-label', `Закрепить долю оффера ${off.name || off.offer_id}`);
      pinBtn.setAttribute('aria-pressed', off.is_pinned ? 'true' : 'false');
      pinBtn.innerHTML = off.is_pinned ? '📌 Pinned' : '📍 Unpinned';
      pinBtn.addEventListener('click', () => {
        updateWeight(stream, off.offer_id, null, !off.is_pinned);
      });
      tdPin.appendChild(pinBtn);

      // State Pill
      const tdState = document.createElement('td');
      if (isRemoved) {
        const reasonText = off.archive_reason ? ` (${off.archive_reason})` : '';
        tdState.innerHTML = `<span class="badge badge-removed" title="${escapeHtml(off.archive_reason || '')}">Removed${escapeHtml(reasonText)}</span>`;
      } else if (off.share === 0) {
        tdState.innerHTML = `<span class="badge badge-zero" title="Трафик не выделен">0% Active</span>`;
      } else {
        tdState.innerHTML = `<span class="badge badge-active">Активен</span>`;
      }

      // Actions
      const tdActions = document.createElement('td');
      tdActions.style.textAlign = 'right';

      if (isRemoved) {
        const bringBackBtn = document.createElement('button');
        bringBackBtn.className = 'btn btn-secondary btn-sm';
        bringBackBtn.textContent = 'Bring back';
        if (!bringBackBtn.dataset) bringBackBtn.dataset = {};
        bringBackBtn.dataset.offerId = String(off.offer_id);
        bringBackBtn.dataset.controlRole = 'bring-back-btn';
        bringBackBtn.setAttribute('data-offer-id', String(off.offer_id));
        bringBackBtn.setAttribute('data-control-role', 'bring-back-btn');
        bringBackBtn.setAttribute('aria-label', `Восстановить оффер ${off.name || off.offer_id}`);
        bringBackBtn.addEventListener('click', () => bringBackOffer(stream, off.offer_id, bringBackBtn));
        tdActions.appendChild(bringBackBtn);
      } else {
        const removeBtn = document.createElement('button');
        removeBtn.className = 'btn btn-secondary btn-sm';
        removeBtn.textContent = 'Remove';
        if (!removeBtn.dataset) removeBtn.dataset = {};
        removeBtn.dataset.offerId = String(off.offer_id);
        removeBtn.dataset.controlRole = 'remove-btn';
        removeBtn.setAttribute('data-offer-id', String(off.offer_id));
        removeBtn.setAttribute('data-control-role', 'remove-btn');
        removeBtn.setAttribute('aria-label', `Удалить оффер ${off.name || off.offer_id}`);
        removeBtn.disabled =
          activeCount <= 1 && stream.offers.filter((o) => o.state === 'active').length <= 1;
        removeBtn.addEventListener('click', () => removeOffer(stream, off.offer_id, removeBtn));
        tdActions.appendChild(removeBtn);
      }

      tr.appendChild(tdName);
      tr.appendChild(tdShare);
      tr.appendChild(tdPin);
      tr.appendChild(tdState);
      tr.appendChild(tdActions);
      tbody.appendChild(tr);
    });

    tableWrapper.appendChild(table);
    card.appendChild(tableWrapper);

    // Footer with Autocomplete Search (Task 3.1 §3 & §5)
    const footer = document.createElement('div');
    footer.className = 'stream-card-footer';

    const addCombobox = document.createElement('div');
    addCombobox.className = 'add-offer-combobox';

    const inputWrapper = document.createElement('div');
    inputWrapper.className = 'combobox-input-wrapper';

    const searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.className = 'form-control inline-search-input';
    searchInput.placeholder = 'Поиск оффера по ID или названию...';
    searchInput.autocomplete = 'off';

    const dropdown = document.createElement('div');
    dropdown.className = 'dropdown-list hidden editor-offer-dropdown';

    inputWrapper.appendChild(searchInput);
    inputWrapper.appendChild(dropdown);

    const addBtn = document.createElement('button');
    addBtn.type = 'button';
    addBtn.className = 'btn btn-secondary btn-sm inline-add-btn';
    addBtn.textContent = 'Add';
    addBtn.disabled = true;

    const combobox = new window.OfferCombobox({
      input: searchInput,
      dropdown: dropdown,
      addBtn: addBtn,
      cardElement: card,
      searchFn: async (query) => {
        const res = await safeFetch(`${apiPrefix}/offers?search=${encodeURIComponent(query)}`);
        if (!res.ok) throw new Error('Search failed');
        return await res.json();
      },
      getExcludedOfferIds: () => {
        // Exclude offers already active in this stream (archived offers can be offered for bring-back)
        return (stream.offers || [])
          .filter((o) => o.state === 'active')
          .map((o) => o.offer_id);
      },
      onSelect: () => {},
      onClear: () => {},
    });

    addBtn.addEventListener('click', async () => {
      const selected = combobox.selectedOffer;
      if (!selected) return;
      const targetId = selected.id;

      const alreadyArchived = (stream.offers || []).find(
        (o) => o.offer_id === targetId && o.state === 'removed'
      );
      if (alreadyArchived) {
        await bringBackOffer(stream, targetId);
      } else {
        await addOffer(stream, targetId);
      }

      combobox.reset();
    });

    addCombobox.appendChild(inputWrapper);
    addCombobox.appendChild(addBtn);
    card._combobox = combobox;

    // Summary stats
    const stats = document.createElement('div');
    stats.className = 'stream-summary-stats';
    const isValidSum = totalActiveShare === 100;
    stats.innerHTML = `
      <span>Активных: ${activeCount}</span> | 
      <span class="${isValidSum ? 'share-valid' : 'share-invalid'}">
        Итого: ${totalActiveShare}%
      </span>`;

    footer.appendChild(addCombobox);
    footer.appendChild(stats);
    card.appendChild(footer);

    return card;
  }

  function getStatusText(status, isManaged) {
    if (!isManaged) return 'Нет редактируемых офферов';
    const isDemo = window.APP_CONFIG && window.APP_CONFIG.is_demo;
    switch (status) {
      case 'clean':
        return isDemo ? 'Синхронизирован с симулятором' : 'Синхронизирован с Keitaro';
      case 'dirty':
        return 'Неопубликованные изменения';
      case 'publishing':
        return 'Публикация...';
      case 'verifying':
        return 'Верификация...';
      case 'conflict':
        return 'Конфликт с трекером';
      case 'unknown':
        return 'Состояние неизвестно';
      case 'remote_missing':
        return 'Удален в трекере';
      default:
        return status;
    }
  }

  // Mutation Handlers
  async function addOffer(stream, offerId) {
    clearNotice();
    try {
      const res = await safeFetch(`${apiPrefix}/streams/${stream.id}/offers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          offer_id: offerId,
          expected_revision: stream.revision,
          expected_remote_campaign_id: currentCampaignId,
        }),
      });
      const updatedStream = await res.json();
      if (!res.ok) throw new Error(updatedStream.detail || 'Ошибка добавления оффера');
      updateStreamInView(updatedStream);
      showToast(`Оффер #${offerId} добавлен в черновик`, 'info');
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  async function removeOffer(stream, offerId, btn = null) {
    clearNotice();
    if (btn) btn.disabled = true;
    try {
      const res = await safeFetch(`${apiPrefix}/streams/${stream.id}/offers/${offerId}/remove`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          expected_revision: stream.revision,
          expected_remote_campaign_id: currentCampaignId,
        }),
      });
      const updatedStream = await res.json();
      if (!res.ok) throw new Error(updatedStream.detail || 'Ошибка удаления оффера');
      updateStreamInView(updatedStream);
      showToast(`Оффер #${offerId} перемещён в архив черновика`, 'info');
    } catch (err) {
      if (err && err.isAppUnreachable) {
        showAppUnreachableNotice();
      }
      showToast(err.message, 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function bringBackOffer(stream, offerId, btn = null) {
    clearNotice();
    if (btn) btn.disabled = true;
    try {
      const res = await safeFetch(`${apiPrefix}/streams/${stream.id}/offers/${offerId}/bring-back`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          expected_revision: stream.revision,
          expected_remote_campaign_id: currentCampaignId,
        }),
      });
      const updatedStream = await res.json();
      if (!res.ok) throw new Error(updatedStream.detail || 'Ошибка возврата оффера');
      updateStreamInView(updatedStream);
      showToast(`Оффер #${offerId} возвращён в активные`, 'info');
    } catch (err) {
      if (err && err.isAppUnreachable) {
        showAppUnreachableNotice();
      }
      showToast(err.message, 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function updateWeight(stream, offerId, share, isPinned) {
    clearNotice();
    try {
      const res = await safeFetch(`${apiPrefix}/streams/${stream.id}/offers/${offerId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          share: share,
          is_pinned: isPinned,
          expected_revision: stream.revision,
          expected_remote_campaign_id: currentCampaignId,
        }),
      });
      const updatedStream = await res.json();
      if (!res.ok) throw new Error(updatedStream.detail || 'Ошибка изменения веса/пина');
      updateStreamInView(updatedStream);
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  async function cancelStream(stream) {
    clearNotice();
    const card = document.getElementById(`stream-card-${stream.id}`);
    const cancelBtn = card ? card.querySelector('.btn-secondary') : null;
    const pushBtn = card ? card.querySelector('.btn-primary') : null;
    if (cancelBtn) cancelBtn.disabled = true;
    if (pushBtn) pushBtn.disabled = true;

    try {
      const res = await safeFetch(`${apiPrefix}/streams/${stream.id}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          expected_remote_campaign_id: currentCampaignId,
        }),
      });
      const updatedStream = await res.json();
      if (!res.ok) throw new Error(updatedStream.detail || 'Ошибка отмены изменений');
      updateStreamInView(updatedStream);
      showToast('Черновик восстановлен до последнего синхронизированного состояния', 'info');
      return true;
    } catch (err) {
      showToast(err.message, 'error');
      return false;
    } finally {
      if (card && card.isConnected) {
        if (cancelBtn) cancelBtn.disabled = false;
        if (pushBtn) pushBtn.disabled = false;
      }
    }
  }

  async function pushStream(stream) {
    clearNotice();
    const card = document.getElementById(`stream-card-${stream.id}`);
    const pushBtn = card ? card.querySelector('.btn-primary') : null;
    const cancelBtn = card ? card.querySelector('.btn-secondary') : null;
    if (pushBtn) pushBtn.disabled = true;
    if (cancelBtn) cancelBtn.disabled = true;

    try {
      const res = await safeFetch(`${apiPrefix}/streams/${stream.id}/push`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          expected_revision: stream.revision,
          expected_remote_campaign_id: currentCampaignId,
        }),
      });
      const updatedStream = await res.json();
      if (!res.ok) {
        const errMsg = updatedStream.detail || 'Ошибка публикации потока';
        if (res.status === 502 || res.status === 504 || /network|timeout|connect/i.test(errMsg)) {
          stream.sync_status = 'unknown';
          updateStreamInView(stream);
          showNotice(
            'Связь прервалась во время публикации. Состояние результата не определено. Нажмите «Проверить результат» для безопасной сверки.',
            'warning'
          );
          const timeoutErr = new Error(errMsg);
          timeoutErr.isNetworkOrTimeout = true;
          throw timeoutErr;
        }
        throw new Error(errMsg);
      }
      updateStreamInView(updatedStream);
      if (updatedStream.sync_status === 'clean') {
        const isDemo = window.APP_CONFIG && window.APP_CONFIG.is_demo;
        const successMsg = isDemo
          ? 'Изменения опубликованы в симуляторе'
          : 'Изменения опубликованы в Keitaro';
        showToast(successMsg, 'success');
      } else if (updatedStream.sync_status === 'unknown') {
        showNotice('Публикация завершилась таймаутом. Требуется сверка состояния потока.', 'warning');
      } else if (updatedStream.sync_status === 'conflict') {
        showNotice('Обнаружен конфликт: состояние потока в трекере изменилось внешне.', 'conflict');
      }
      return true;
    } catch (err) {
      if (err.isAppUnreachable) {
        stream.sync_status = 'unknown';
        updateStreamInView(stream);
        showNotice(
          'Связь с приложением прервалась во время публикации. Состояние результата не определено. Проверьте, что сервер запущен, и нажмите «Проверить результат».',
          'warning'
        );
      } else if (err.isNetworkOrTimeout) {
        // Persistent context notice already displayed in editorNotice
      } else if (err.message && err.message.includes('Conflict')) {
        showNotice(err.message, 'conflict');
      } else {
        showToast(err.message, 'error');
      }
      return false;
    } finally {
      if (card && card.isConnected) {
        if (pushBtn && stream.sync_status !== 'unknown') pushBtn.disabled = false;
        if (cancelBtn) cancelBtn.disabled = false;
      }
    }
  }

  async function reconcileStream(stream) {
    clearNotice();
    const card = document.getElementById(`stream-card-${stream.id}`);
    const btn = card ? card.querySelector('.btn-primary') : null;
    if (btn) btn.disabled = true;

    try {
      const res = await safeFetch(`${apiPrefix}/streams/${stream.id}/reconcile`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const updatedStream = await res.json();
      if (!res.ok) throw new Error(updatedStream.detail || 'Ошибка сверки состояния потока');
      updateStreamInView(updatedStream);
      if (updatedStream.sync_status === 'clean') {
        showToast('Сверка завершена: поток синхронизирован с трекером', 'success');
      } else if (updatedStream.sync_status === 'dirty') {
        showToast('Сверка завершена: изменения сохранены локально, можно повторить публикацию', 'info');
      } else if (updatedStream.sync_status === 'conflict') {
        showNotice('Конфликт с трекером: удалённое состояние изменилось', 'conflict');
      } else {
        showToast(`Статус потока после сверки: ${getStatusText(updatedStream.sync_status, updatedStream.is_managed)}`, 'warning');
      }
      return true;
    } catch (err) {
      showToast(err.message, 'error');
      return false;
    } finally {
      if (btn && card && card.isConnected) btn.disabled = false;
    }
  }

  function updateStreamInView(updatedStream) {
    if (currentCampaignData && currentCampaignData.streams) {
      const idx = currentCampaignData.streams.findIndex((s) => s.id === updatedStream.id);
      if (idx !== -1) {
        currentCampaignData.streams[idx] = updatedStream;
      }
    }
    const oldCard = document.getElementById(`stream-card-${updatedStream.id}`);
    if (oldCard) {
      // Check if active element was inside oldCard to restore focus after replacement
      let focusedSelector = null;
      let selectionStart = null;
      let selectionEnd = null;
      const activeEl = document.activeElement;
      if (activeEl && oldCard.contains(activeEl)) {
        if (activeEl.dataset && activeEl.dataset.offerId && activeEl.dataset.controlRole) {
          focusedSelector = `[data-offer-id="${activeEl.dataset.offerId}"][data-control-role="${activeEl.dataset.controlRole}"]`;
          if (activeEl.selectionStart !== undefined) {
            selectionStart = activeEl.selectionStart;
            selectionEnd = activeEl.selectionEnd;
          }
        }
      }

      if (oldCard._combobox && typeof oldCard._combobox.destroy === 'function') {
        oldCard._combobox.destroy();
      }
      const newCard = createStreamCard(updatedStream);
      oldCard.replaceWith(newCard);

      if (focusedSelector) {
        const nextEl = newCard.querySelector(focusedSelector);
        if (nextEl) {
          nextEl.focus();
          if (selectionStart !== null && nextEl.setSelectionRange) {
            try {
              nextEl.setSelectionRange(selectionStart, selectionEnd);
            } catch (e) {}
          }
        }
      }
    }
  }

  // Expose state for connection_mode.js dirty & in-flight guards
  window.EDITOR_STATE = {
    get currentCampaignData() {
      return currentCampaignData;
    },
    pushAllDirty: async () => {
      if (!currentCampaignData || !currentCampaignData.streams) return;
      const dirty = currentCampaignData.streams.filter((s) => s.sync_status === 'dirty');
      for (const s of dirty) {
        await pushStream(s);
      }
    },
    cancelAllDirty: async () => {
      if (!currentCampaignData || !currentCampaignData.streams) return;
      const dirty = currentCampaignData.streams.filter((s) => s.sync_status === 'dirty');
      for (const s of dirty) {
        await cancelStream(s);
      }
    },
  };
});
