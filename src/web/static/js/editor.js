document.addEventListener('DOMContentLoaded', () => {
  const campaignSelect = document.getElementById('campaignSelect');
  const fetchStreamsBtn = document.getElementById('fetchStreamsBtn');
  const fetchSpinner = document.getElementById('fetchSpinner');
  const lastSyncTime = document.getElementById('lastSyncTime');
  const streamsContainer = document.getElementById('streamsContainer');
  const editorNotice = document.getElementById('editorNotice');
  const viewInKtLink = document.getElementById('viewInKtLink');

  let currentCampaignId = null;
  let currentCampaignData = null;

  // Check URL query parameters
  const urlParams = new URLSearchParams(window.location.search);
  const paramCampaignId = urlParams.get('campaign_id');
  const paramNotice = urlParams.get('notice');

  if (paramNotice === 'creator_unavailable_in_real_mode') {
    showNotice(
      'Создание кампаний через интерфейс доступно только в демо-режиме (KT_MODE=demo). В рабочем режиме Keitaro поддерживается только редактирование существующих кампаний.',
      'info'
    );
  }

  // Load campaigns list
  async function loadCampaigns() {
    try {
      const res = await fetch('/api/campaigns');
      if (!res.ok) throw new Error('Failed to load campaigns');
      const campaigns = await res.json();

      campaignSelect.innerHTML = '<option value="">-- Выберите кампанию --</option>';
      campaigns.forEach((c) => {
        const isSelected =
          paramCampaignId && parseInt(paramCampaignId, 10) === c.remote_campaign_id;
        const selectedAttr = isSelected ? 'selected' : '';
        campaignSelect.innerHTML += `<option value="${c.remote_campaign_id}" ${selectedAttr}>[ID: ${c.remote_campaign_id}] ${escapeHtml(c.name)}</option>`;
      });

      // Synchronize context and load campaign data
      if (paramCampaignId) {
        currentCampaignId = parseInt(paramCampaignId, 10);
        campaignSelect.value = currentCampaignId;
        fetchStreamsBtn.disabled = false;
        await loadCampaignData(currentCampaignId);
      } else if (window.APP_CONFIG && window.APP_CONFIG.is_demo && campaigns.length > 0) {
        currentCampaignId = campaigns[0].remote_campaign_id;
        campaignSelect.value = currentCampaignId;
        fetchStreamsBtn.disabled = false;
        await loadCampaignData(currentCampaignId);
      }
    } catch (err) {
      console.warn('Could not load campaigns:', err);
      showToast('Не удалось загрузить список кампаний', 'error');
    }
  }
  loadCampaigns();

  // Load local draft / baseline for selected campaign without upstream fetch
  async function loadCampaignData(campaignId) {
    clearNotice();
    updateViewInKtLink(campaignId);

    try {
      const res = await fetch(`/api/campaigns/${campaignId}`);
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
      currentCampaignData = data;
      renderCampaignStreams(data);
      updateSyncTimeDisplay(data.last_sync_at);
    } catch (err) {
      console.warn('Error loading local campaign state:', err);
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
      viewInKtLink.href = `${cleanBase}/admin/#!/campaigns/${remoteCampaignId}`;
      viewInKtLink.classList.remove('hidden');
    } else {
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
      fetchStreamsBtn.disabled = false;
      const newUrl = new URL(window.location);
      newUrl.searchParams.set('campaign_id', currentCampaignId);
      window.history.replaceState({}, '', newUrl);
      await loadCampaignData(currentCampaignId);
    } else {
      currentCampaignId = null;
      currentCampaignData = null;
      fetchStreamsBtn.disabled = true;
      const newUrl = new URL(window.location);
      newUrl.searchParams.delete('campaign_id');
      window.history.replaceState({}, '', newUrl);
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
      const res = await fetch(`/api/campaigns/${campaignId}/fetch-streams`, {
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

      currentCampaignData = data;
      renderCampaignStreams(data);
      updateSyncTimeDisplay(data.last_sync_at || new Date().toISOString());

      const isDemo = window.APP_CONFIG && window.APP_CONFIG.is_demo;
      showToast(
        isDemo ? 'Потоки загружены из демо-симулятора' : 'Потоки успешно синхронизированы с Keitaro',
        'success'
      );
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      fetchStreamsBtn.disabled = false;
      fetchSpinner.classList.add('hidden');
    }
  }

  function showNotice(msg, type = 'info') {
    editorNotice.className = `editor-notice notice-${type}`;
    editorNotice.textContent = msg;
    editorNotice.classList.remove('hidden');
  }

  function clearNotice() {
    editorNotice.classList.add('hidden');
    editorNotice.textContent = '';
  }

  function renderCampaignStreams(campaign) {
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

  // Compact row for streams without editable offers (Flow 1 / redirect / opaque)
  function createUnmanagedStreamRow(stream) {
    const row = document.createElement('div');
    row.className = 'unmanaged-stream-row';
    row.id = `stream-row-${stream.id}`;

    const titleInfo = document.createElement('div');
    titleInfo.className = 'unmanaged-stream-info';

    const title = document.createElement('span');
    title.className = 'unmanaged-stream-name';
    title.textContent = stream.name;

    const idBadge = document.createElement('span');
    idBadge.className = 'stream-id-badge';
    idBadge.textContent = `Stream ID: ${stream.remote_stream_id}`;

    titleInfo.appendChild(title);
    titleInfo.appendChild(idBadge);

    const pill = document.createElement('span');
    pill.className = 'unmanaged-stream-pill';
    pill.textContent = 'Нет редактируемых офферов';

    row.appendChild(titleInfo);
    row.appendChild(pill);
    return row;
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
      pushBtn.className = 'btn btn-primary btn-sm';
      pushBtn.textContent = isDemo ? 'Push to Demo' : 'Push to KT';
      if (isDemo) {
        pushBtn.setAttribute(
          'data-tooltip',
          'Опубликовать активные офферы в автономный демо-симулятор'
        );
      } else {
        pushBtn.setAttribute('data-tooltip', 'Опубликовать активные офферы в Keitaro');
      }
      pushBtn.disabled = !isDirty || stream.sync_status === 'publishing';
      pushBtn.addEventListener('click', () => pushStream(stream));

      const cancelBtn = document.createElement('button');
      cancelBtn.className = 'btn btn-secondary btn-sm';
      cancelBtn.textContent = 'Cancel';
      cancelBtn.disabled = !isDirty || stream.sync_status === 'publishing';
      cancelBtn.addEventListener('click', () => cancelStream(stream));

      actions.appendChild(pushBtn);
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
      tdName.innerHTML = `
        <div class="offer-name-cell">${escapeHtml(off.name)}</div>
        <div class="offer-id-sub">Offer ID: ${off.offer_id}</div>`;

      // Share Input
      const tdShare = document.createElement('td');
      if (isRemoved) {
        tdShare.innerHTML = '<span>0%</span>';
      } else {
        const wrapper = document.createElement('div');
        wrapper.className = 'weight-input-wrapper';
        const input = document.createElement('input');
        input.type = 'number';
        input.min = '0';
        input.max = '100';
        input.className = 'form-control weight-input';
        input.value = off.share;
        input.addEventListener('change', () => {
          const newShare = parseInt(input.value, 10);
          if (!isNaN(newShare)) {
            updateWeight(stream, off.offer_id, newShare, true);
          }
        });
        wrapper.appendChild(input);
        wrapper.appendChild(document.createTextNode('%'));
        tdShare.appendChild(wrapper);
      }

      // Pin Toggle Button
      const tdPin = document.createElement('td');
      const pinBtn = document.createElement('button');
      pinBtn.type = 'button';
      pinBtn.className = 'btn-pin';
      pinBtn.disabled = isRemoved;
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
        bringBackBtn.addEventListener('click', () => bringBackOffer(stream, off.offer_id));
        tdActions.appendChild(bringBackBtn);
      } else {
        const removeBtn = document.createElement('button');
        removeBtn.className = 'btn btn-secondary btn-sm';
        removeBtn.textContent = 'Remove';
        removeBtn.disabled =
          activeCount <= 1 && stream.offers.filter((o) => o.state === 'active').length <= 1;
        removeBtn.addEventListener('click', () => removeOffer(stream, off.offer_id));
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

    // Footer with Autocomplete Search
    const footer = document.createElement('div');
    footer.className = 'stream-card-footer';

    const addCombobox = document.createElement('div');
    addCombobox.className = 'add-offer-combobox';

    const inputWrapper = document.createElement('div');
    inputWrapper.style.position = 'relative';
    inputWrapper.style.width = '280px';

    const searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.className = 'form-control inline-search-input';
    searchInput.placeholder = 'Поиск оффера по ID или названию...';
    searchInput.autocomplete = 'off';
    searchInput.style.height = '32px';

    const dropdown = document.createElement('div');
    dropdown.className = 'dropdown-list hidden editor-offer-dropdown';

    inputWrapper.appendChild(searchInput);
    inputWrapper.appendChild(dropdown);

    const addBtn = document.createElement('button');
    addBtn.type = 'button';
    addBtn.className = 'btn btn-secondary btn-sm inline-add-btn';
    addBtn.textContent = 'Add';
    addBtn.disabled = true;

    let selectedOffer = null;
    let debounceTimer = null;

    searchInput.addEventListener('input', () => {
      clearTimeout(debounceTimer);
      selectedOffer = null;
      addBtn.disabled = true;

      const q = searchInput.value.trim();
      if (q.length < 1) {
        dropdown.classList.add('hidden');
        return;
      }

      debounceTimer = setTimeout(async () => {
        try {
          const res = await fetch(`/api/offers?search=${encodeURIComponent(q)}`);
          if (!res.ok) throw new Error('Search failed');
          const offers = await res.json();
          renderEditorOfferDropdown(offers, dropdown, searchInput, addBtn, (chosen) => {
            selectedOffer = chosen;
          });
        } catch (err) {
          console.warn('Editor offer search error:', err);
        }
      }, 250);
    });

    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        if (!selectedOffer) {
          const firstItem = dropdown.querySelector('.dropdown-item[data-id]');
          if (firstItem) {
            firstItem.click();
            addBtn.click();
          }
        } else {
          addBtn.click();
        }
      } else if (e.key === 'Escape') {
        dropdown.classList.add('hidden');
      }
    });

    document.addEventListener('click', (e) => {
      if (!inputWrapper.contains(e.target)) {
        dropdown.classList.add('hidden');
      }
    });

    addBtn.addEventListener('click', async () => {
      if (!selectedOffer) return;
      const targetId = selectedOffer.id;

      const alreadyActive = stream.offers.find(
        (o) => o.offer_id === targetId && o.state === 'active'
      );
      if (alreadyActive) {
        showToast(`Оффер [ID: ${targetId}] уже активен в этом потоке`, 'warning');
        return;
      }

      const alreadyArchived = stream.offers.find(
        (o) => o.offer_id === targetId && o.state === 'removed'
      );
      if (alreadyArchived) {
        await bringBackOffer(stream, targetId);
      } else {
        await addOffer(stream, targetId);
      }

      searchInput.value = '';
      selectedOffer = null;
      addBtn.disabled = true;
      dropdown.classList.add('hidden');
    });

    addCombobox.appendChild(inputWrapper);
    addCombobox.appendChild(addBtn);

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

  function renderEditorOfferDropdown(offers, dropdown, searchInput, addBtn, onSelect) {
    if (!offers || offers.length === 0) {
      dropdown.innerHTML = '<div class="dropdown-item">Ничего не найдено</div>';
      dropdown.classList.remove('hidden');
      return;
    }

    dropdown.innerHTML = offers
      .map(
        (o) =>
          `<div class="dropdown-item" data-id="${o.id}" data-name="${escapeHtml(o.name)}">[ID: ${o.id}] ${escapeHtml(o.name)}</div>`
      )
      .join('');
    dropdown.classList.remove('hidden');

    dropdown.querySelectorAll('.dropdown-item[data-id]').forEach((el) => {
      el.addEventListener('click', () => {
        const id = parseInt(el.getAttribute('data-id'), 10);
        const name = el.getAttribute('data-name');
        if (id) {
          const chosen = { id, name };
          onSelect(chosen);
          searchInput.value = `[ID: ${id}] ${name}`;
          addBtn.disabled = false;
          dropdown.classList.add('hidden');
        }
      });
    });
  }

  function getStatusText(status, isManaged) {
    if (!isManaged) return 'Нет редактируемых офферов';
    const isDemo = window.APP_CONFIG && window.APP_CONFIG.is_demo;
    switch (status) {
      case 'clean':
        return isDemo ? 'Синхронизирован с демо' : 'Синхронизирован с KT';
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
      const res = await fetch(`/api/streams/${stream.id}/offers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ offer_id: offerId, expected_revision: stream.revision }),
      });
      const updatedStream = await res.json();
      if (!res.ok) throw new Error(updatedStream.detail || 'Ошибка добавления оффера');
      updateStreamInView(updatedStream);
      showToast(`Оффер #${offerId} добавлен в черновик`, 'info');
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  async function removeOffer(stream, offerId) {
    clearNotice();
    try {
      const res = await fetch(`/api/streams/${stream.id}/offers/${offerId}/remove`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ expected_revision: stream.revision }),
      });
      const updatedStream = await res.json();
      if (!res.ok) throw new Error(updatedStream.detail || 'Ошибка удаления оффера');
      updateStreamInView(updatedStream);
      showToast(`Оффер #${offerId} перемещён в архив черновика`, 'info');
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  async function bringBackOffer(stream, offerId) {
    clearNotice();
    try {
      const res = await fetch(`/api/streams/${stream.id}/offers/${offerId}/bring-back`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ expected_revision: stream.revision }),
      });
      const updatedStream = await res.json();
      if (!res.ok) throw new Error(updatedStream.detail || 'Ошибка возврата оффера');
      updateStreamInView(updatedStream);
      showToast(`Оффер #${offerId} возвращён в активные`, 'info');
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  async function updateWeight(stream, offerId, share, isPinned) {
    clearNotice();
    try {
      const res = await fetch(`/api/streams/${stream.id}/offers/${offerId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          share: share,
          is_pinned: isPinned,
          expected_revision: stream.revision,
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
    try {
      const res = await fetch(`/api/streams/${stream.id}/cancel`, {
        method: 'POST',
      });
      const updatedStream = await res.json();
      if (!res.ok) throw new Error(updatedStream.detail || 'Ошибка отмены изменений');
      updateStreamInView(updatedStream);
      showToast('Черновик сброшен до исходного состояния Baseline', 'info');
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  async function pushStream(stream) {
    clearNotice();
    try {
      const res = await fetch(`/api/streams/${stream.id}/push`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ expected_revision: stream.revision }),
      });
      const updatedStream = await res.json();
      if (!res.ok) throw new Error(updatedStream.detail || 'Ошибка публикации потока');
      updateStreamInView(updatedStream);
      const isDemo = window.APP_CONFIG && window.APP_CONFIG.is_demo;
      const successMsg = isDemo
        ? `Поток успешно опубликован в демо-трекер (Flow ID: ${updatedStream.id}) и подтверждён read-back!`
        : 'Поток успешно опубликован в Keitaro и подтверждён read-back!';
      showToast(successMsg, 'success');
    } catch (err) {
      showToast(err.message, 'error');
      if (err.message.includes('Conflict')) {
        showNotice(err.message, 'conflict');
      }
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
      const newCard = createStreamCard(updatedStream);
      oldCard.replaceWith(newCard);
    }
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
  }

  // --- Demo Controls Handling (strictly active in demo mode) ---
  const demoResetBtn = document.getElementById('demoResetBtn');
  if (demoResetBtn) {
    demoResetBtn.addEventListener('click', async () => {
      const ok = confirm(
        'Сбросить демо-кампании, локальную БД и симулятор к исходному состоянию? Все несохраненные черновики будут удалены.'
      );
      if (!ok) return;

      try {
        demoResetBtn.disabled = true;
        const res = await fetch('/api/demo/reset', { method: 'POST' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Ошибка сброса демо');
        showToast('Демо-состояние успешно сброшено к исходному seed (34/33/33)!', 'success');
        clearNotice();
        await loadCampaigns();
      } catch (err) {
        showToast(err.message, 'error');
      } finally {
        demoResetBtn.disabled = false;
      }
    });
  }

  const demoSimulateDeleteBtn = document.getElementById('demoSimulateDeleteBtn');
  if (demoSimulateDeleteBtn) {
    demoSimulateDeleteBtn.addEventListener('click', async () => {
      if (!currentCampaignData || !currentCampaignData.streams) {
        showToast('Сначала выберите кампанию с управляемым потоком', 'warning');
        return;
      }

      const managedStream = currentCampaignData.streams.find((s) => s.is_managed);
      if (!managedStream) {
        showToast('В текущей кампании нет управляемого потока', 'warning');
        return;
      }

      // Check for offer 105 or last active offer in this stream
      const activeOffers = managedStream.offers.filter((o) => o.state === 'active');
      if (activeOffers.length === 0) {
        showToast('В управляемом потоке нет активных офферов для удаления', 'warning');
        return;
      }

      const offer105 = activeOffers.find((o) => o.offer_id === 105);
      const targetOffer = offer105 || activeOffers[activeOffers.length - 1];

      try {
        demoSimulateDeleteBtn.disabled = true;
        const res = await fetch('/api/demo/simulate-delete-stream-offer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            stream_id: managedStream.remote_stream_id,
            offer_id: targetOffer.offer_id,
          }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Ошибка симуляции удаления');

        showToast(
          `Привязка оффера ID ${targetOffer.offer_id} удалена из потока в симуляторе!`,
          'info'
        );
        showNotice(
          `Внимание: В симуляторе из потока Flow 2 удалена привязка оффера ID ${targetOffer.offer_id}. Нажмите «Fetch Streams», чтобы зафиксировать внешнее изменение.`,
          'warning'
        );
      } catch (err) {
        showToast(err.message, 'error');
      } finally {
        demoSimulateDeleteBtn.disabled = false;
      }
    });
  }

  const demoSimulateDisableBtn = document.getElementById('demoSimulateDisableBtn');
  let offer105Disabled = false;
  if (demoSimulateDisableBtn) {
    demoSimulateDisableBtn.addEventListener('click', async () => {
      try {
        demoSimulateDisableBtn.disabled = true;
        const targetAvailable = offer105Disabled; // toggle
        const res = await fetch('/api/demo/simulate-disable-global-offer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ offer_id: 105, available: targetAvailable }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Ошибка изменения статуса оффера');

        offer105Disabled = !targetAvailable;
        if (offer105Disabled) {
          demoSimulateDisableBtn.textContent = '✅ Восстановить оффер ID 105 в каталоге';
          showToast(
            'Оффер ID 105 скрыт в глобальном каталоге симулятора. Теперь при попытке Bring back будет показано объяснение!',
            'info'
          );
        } else {
          demoSimulateDisableBtn.textContent = '🚫 Скрыть оффер ID 105 в каталоге';
          showToast('Оффер ID 105 снова доступен в каталоге симулятора.', 'success');
        }
      } catch (err) {
        showToast(err.message, 'error');
      } finally {
        demoSimulateDisableBtn.disabled = false;
      }
    });
  }
});
