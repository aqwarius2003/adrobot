document.addEventListener('DOMContentLoaded', () => {
  const campaignSelect = document.getElementById('campaignSelect');
  const fetchStreamsBtn = document.getElementById('fetchStreamsBtn');
  const fetchSpinner = document.getElementById('fetchSpinner');
  const lastSyncTime = document.getElementById('lastSyncTime');
  const streamsContainer = document.getElementById('streamsContainer');
  const editorNotice = document.getElementById('editorNotice');

  let currentCampaignId = null;
  let currentCampaignData = null;

  // Check URL query parameters for auto-selection
  const urlParams = new URLSearchParams(window.location.search);
  const paramCampaignId = urlParams.get('campaign_id');

  // Load campaigns list
  async function loadCampaigns() {
    try {
      const res = await fetch('/api/campaigns');
      if (!res.ok) throw new Error('Failed to load campaigns');
      const campaigns = await res.json();

      campaignSelect.innerHTML = '<option value="">-- Выберите кампанию --</option>';
      campaigns.forEach(c => {
        const selected = (paramCampaignId && parseInt(paramCampaignId, 10) === c.remote_campaign_id) ? 'selected' : '';
        campaignSelect.innerHTML += `<option value="${c.remote_campaign_id}" ${selected}>[ID: ${c.remote_campaign_id}] ${c.name}</option>`;
      });

      if (paramCampaignId) {
        fetchStreamsBtn.disabled = false;
        fetchStreams(parseInt(paramCampaignId, 10));
      }
    } catch (err) {
      console.warn('Could not load campaigns:', err);
      showToast('Не удалось загрузить список кампаний', 'error');
    }
  }
  loadCampaigns();

  campaignSelect.addEventListener('change', () => {
    const val = campaignSelect.value;
    if (val) {
      currentCampaignId = parseInt(val, 10);
      fetchStreamsBtn.disabled = false;
    } else {
      currentCampaignId = null;
      fetchStreamsBtn.disabled = true;
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
          showNotice(data.detail || 'Невозможно выполнить Fetch: имеются неопубликованные изменения.', 'conflict');
        }
        throw new Error(data.detail || 'Ошибка при загрузке потоков из Keitaro');
      }

      currentCampaignData = data;
      renderCampaignStreams(data);
      lastSyncTime.textContent = new Date().toLocaleTimeString();
      showToast('Потоки успешно синхронизированы с Keitaro', 'success');
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
          <p>В Keitaro не найдено потоков для этой кампании.</p>
        </div>`;
      return;
    }

    campaign.streams.forEach(stream => {
      const card = createStreamCard(stream);
      streamsContainer.appendChild(card);
    });
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
      const pushBtn = document.createElement('button');
      pushBtn.className = 'btn btn-primary btn-sm';
      pushBtn.textContent = 'Push to KT';
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

    // Body
    if (!stream.is_managed) {
      const opaqueBody = document.createElement('div');
      opaqueBody.className = 'opaque-stream-body';
      opaqueBody.innerHTML = `
        <svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" stroke-width="2" fill="none"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>
        <span>Поток без редактируемых офферов (HTTP Redirect / Фильтры). Отображается только для чтения.</span>`;
      card.appendChild(opaqueBody);
      return card;
    }

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

    stream.offers.forEach(off => {
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
        tdState.innerHTML = `<span class="badge badge-removed">Removed</span>`;
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
        removeBtn.disabled = activeCount <= 1 && stream.offers.filter(o => o.state === 'active').length <= 1;
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

    // Footer
    const footer = document.createElement('div');
    footer.className = 'stream-card-footer';

    // Add Offer inline search
    const addInline = document.createElement('div');
    addInline.className = 'add-offer-inline';
    addInline.innerHTML = `
      <input type="text" class="form-control inline-search-input" placeholder="Добавить оффер по ID или названию..." style="height: 30px;">
      <button type="button" class="btn btn-secondary btn-sm inline-add-btn">Add</button>`;

    const inlineInput = addInline.querySelector('.inline-search-input');
    const inlineAddBtn = addInline.querySelector('.inline-add-btn');

    inlineAddBtn.addEventListener('click', async () => {
      const q = inlineInput.value.trim();
      if (!q) return;
      const offId = parseInt(q, 10);
      if (isNaN(offId)) {
        showToast('Пожалуйста, введите числовой ID оффера для добавления', 'error');
        return;
      }
      await addOffer(stream, offId);
      inlineInput.value = '';
    });

    // Summary stats
    const stats = document.createElement('div');
    stats.className = 'stream-summary-stats';
    const isValidSum = totalActiveShare === 100;
    stats.innerHTML = `
      <span>Активных: ${activeCount}</span> | 
      <span class="${isValidSum ? 'share-valid' : 'share-invalid'}">
        Итого: ${totalActiveShare}%
      </span>`;

    footer.appendChild(addInline);
    footer.appendChild(stats);
    card.appendChild(footer);

    return card;
  }

  function getStatusText(status, isManaged) {
    if (!isManaged) return 'Opaque / Redirect';
    switch (status) {
      case 'clean': return 'Синхронизирован с KT';
      case 'dirty': return 'Unpublished changes';
      case 'publishing': return 'Публикация в KT...';
      case 'verifying': return 'Верификация...';
      case 'conflict': return 'Конфликт с KT';
      case 'unknown': return 'Проверка результата...';
      case 'remote_missing': return 'Удалён в KT';
      default: return status;
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
      showToast('Черновик сброшен до последнего подтверждённого состояния KT', 'info');
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
      if (!res.ok) throw new Error(updatedStream.detail || 'Ошибка публикации потока в KT');
      updateStreamInView(updatedStream);
      showToast('Поток успешно опубликован в Keitaro и подтверждён!', 'success');
    } catch (err) {
      showToast(err.message, 'error');
      if (err.message.includes('Conflict')) {
        showNotice(err.message, 'conflict');
      }
    }
  }

  function updateStreamInView(updatedStream) {
    const oldCard = document.getElementById(`stream-card-${updatedStream.id}`);
    if (oldCard) {
      const newCard = createStreamCard(updatedStream);
      oldCard.replaceWith(newCard);
    }
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
});
