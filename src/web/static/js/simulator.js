// Keitaro Simulator Tab Admin Interface (DEMO_MODE_TASK.md v3.1 & Customer Spec)
document.addEventListener('DOMContentLoaded', () => {
  const campaignSelect = document.getElementById('simCampaignSelect');
  const goToAdrobotBtn = document.getElementById('simGoToAdrobotBtn');
  const simNotice = document.getElementById('simNotice');
  const simLoadingState = document.getElementById('simLoadingState');
  const editorCard = document.getElementById('simEditorCard');
  const emptyState = document.getElementById('simEmptyState');
  const flow1Stub = document.getElementById('simFlow1Stub');
  const flow1TitleEl = document.getElementById('simFlow1Title');
  const flow1IdText = document.getElementById('simFlow1IdText');
  const flow1ClicksText = document.getElementById('simFlow1ClicksText');

  const streamNameEl = document.getElementById('simCurrentStreamName');
  const streamIdBadge = document.getElementById('simCurrentStreamIdBadge');
  const unsavedBadge = document.getElementById('simUnsavedBadge');
  const offersTbody = document.getElementById('simOffersTbody');

  const searchInput = document.getElementById('simOfferSearchInput');
  const searchDropdown = document.getElementById('simOfferDropdown');
  const addOfferBtn = document.getElementById('simAddOfferBtn');

  const activeCountEl = document.getElementById('simActiveCount');
  const totalShareEl = document.getElementById('simTotalShare');
  const saveBtn = document.getElementById('simSaveBtn');
  const saveSpinner = document.getElementById('simSaveSpinner');
  const revertBtn = document.getElementById('simRevertBtn');

  const safeFetch =
    (window.apiClient && window.apiClient.fetch) ||
    window.apiFetch ||
    (typeof window.fetch === 'function' ? window.fetch.bind(window) : fetch);

  let currentCampaignId = null;
  let currentStreams = [];
  let currentFlow2Stream = null;
  let currentStreamId = null;
  let initialStreamSnapshot = null;
  let formOffers = [];
  let isFormDirty = false;
  let offerCombobox = null;
  let currentLoadRequestId = 0;

  // Catalog cache for resolving full offer names (e.g. "Demo Offer 1 (Gambling CPA)")
  const catalogOffersMap = new Map();
  let catalogLoaded = false;

  // Check URL params
  const urlParams = new URLSearchParams(window.location.search);
  const paramCampaignId = urlParams.get('campaign_id');

  // Initialize OfferCombobox component
  if (window.OfferCombobox && searchInput && searchDropdown && addOfferBtn) {
    offerCombobox = new window.OfferCombobox({
      input: searchInput,
      dropdown: searchDropdown,
      addBtn: addOfferBtn,
      cardElement: editorCard,
      searchFn: async (query) => {
        const res = await safeFetch(`/api/demo/simulator/offers?search=${encodeURIComponent(query)}`);
        if (!res.ok) throw new Error('Ошибка поиска офферов');
        const items = await res.json();
        items.forEach((o) => {
          if (o.id && o.name) catalogOffersMap.set(o.id, o.name);
        });
        return items;
      },
      getExcludedOfferIds: () => {
        // Exclude offers already attached to current stream
        return formOffers.map((o) => o.offer_id);
      },
      onSelect: () => {},
      onClear: () => {},
    });
  }

  // Pre-load simulator catalog once to resolve existing offer names
  async function ensureCatalogLoaded() {
    if (catalogLoaded) return;
    try {
      const res = await safeFetch('/api/demo/simulator/offers');
      if (res.ok) {
        const offers = await res.json();
        offers.forEach((o) => {
          if (o.id && o.name) catalogOffersMap.set(o.id, o.name);
        });
        catalogLoaded = true;
      }
    } catch (e) {
      console.warn('Could not pre-load simulator offers catalog:', e);
    }
  }

  // Load campaigns from simulator API
  async function loadCampaigns() {
    try {
      await ensureCatalogLoaded();
      const res = await safeFetch('/api/demo/simulator/campaigns');
      if (!res.ok) throw new Error('Не удалось загрузить список кампаний из симулятора');
      const campaigns = await res.json();

      campaignSelect.innerHTML = '<option value="">-- Выберите кампанию --</option>';

      const savedCampaignId = localStorage.getItem('adrobot_selected_campaign_demo');
      const targetId = paramCampaignId
        ? parseInt(paramCampaignId, 10)
        : savedCampaignId
        ? parseInt(savedCampaignId, 10)
        : null;

      campaigns.forEach((c) => {
        const opt = document.createElement('option');
        opt.value = String(c.id);
        opt.textContent = `[ID: ${c.id}] ${c.name || ''}`;
        if (targetId && c.id === targetId) {
          opt.selected = true;
        }
        campaignSelect.appendChild(opt);
      });

      if (targetId && campaigns.some((c) => c.id === targetId)) {
        currentCampaignId = targetId;
        await loadCampaignStreams(currentCampaignId);
      } else if (campaigns.length > 0) {
        currentCampaignId = campaigns[0].id;
        campaignSelect.value = currentCampaignId;
        await loadCampaignStreams(currentCampaignId);
      } else {
        renderEmptyState();
      }
    } catch (err) {
      console.error('Failed to load simulator campaigns:', err);
      showNotice(err.message, 'conflict');
    }
  }

  // Identify Flow 2 and Flow 1 streams deterministically (Task 3.1 §1)
  function resolveStreams(streams) {
    let flow2Candidates = streams.filter((s) => s.role === 'flow_2');
    let flow1Candidates = streams.filter((s) => s.role === 'flow_1');

    // Safe migration/fallback for streams without explicit role attribute
    if (flow2Candidates.length === 0 && flow1Candidates.length === 0) {
      const defCandidates = streams.filter(
        (s) => s.type === 'default' || s.schema === 'landings_offers' || (s.name && s.name.includes('Flow 2'))
      );
      const regCandidates = streams.filter(
        (s) => s.type === 'regular' || s.schema === 'redirect' || (s.name && s.name.includes('Flow 1'))
      );

      if (defCandidates.length === 1) {
        flow2Candidates = defCandidates;
      }
      if (regCandidates.length === 1) {
        flow1Candidates = regCandidates;
      }
    }

    return { flow2Candidates, flow1Candidates };
  }

  // Load streams for chosen campaign and display Flow 2 & Flow 1
  async function loadCampaignStreams(campaignId) {
    const thisRequestId = ++currentLoadRequestId;
    clearNotice();
    if (offerCombobox) offerCombobox.reset();

    // Explicit loading state: immediately hide previous tables and disable/hide stream actions
    editorCard.classList.add('hidden');
    if (flow1Stub) flow1Stub.classList.add('hidden');
    emptyState.classList.add('hidden');
    if (simLoadingState) simLoadingState.classList.remove('hidden');

    isFormDirty = false;
    updateFormDirtyState();

    try {
      await ensureCatalogLoaded();

      const res = await safeFetch(`/api/demo/simulator/campaigns/${campaignId}/streams`);
      if (!res.ok) throw new Error('Не удалось загрузить потоки кампании');
      const streams = await res.json();

      // Guard against stale response if user switched campaigns while in flight
      if (thisRequestId !== currentLoadRequestId || currentCampaignId !== campaignId) {
        return;
      }

      if (simLoadingState) simLoadingState.classList.add('hidden');
      currentStreams = streams;

      const { flow2Candidates, flow1Candidates } = resolveStreams(streams);

      // Handle Flow 1 compact stub below Flow 2 (Customer exact format)
      if (flow1Candidates.length > 0 && flow1Stub) {
        const flow1 = flow1Candidates[0];
        const pos = flow1.position !== undefined ? flow1.position : 1;
        if (flow1TitleEl) flow1TitleEl.textContent = `Stream: Flow ${pos} (#${pos})`;
        if (flow1IdText) flow1IdText.textContent = flow1.id;
        if (flow1ClicksText) {
          flow1ClicksText.textContent = flow1.clicks_today !== undefined ? flow1.clicks_today : '—';
        }
        flow1Stub.classList.remove('hidden');
      } else if (flow1Stub) {
        flow1Stub.classList.add('hidden');
      }

      // Check Flow 2 existence and uniqueness
      if (flow2Candidates.length === 0) {
        editorCard.classList.add('hidden');
        emptyState.classList.add('hidden');
        showNotice('В этой демо-кампании не настроен основной поток (Flow 2). Редактирование невозможно.', 'conflict');
        return;
      }

      if (flow2Candidates.length > 1) {
        editorCard.classList.add('hidden');
        emptyState.classList.add('hidden');
        showNotice(
          'Обнаружено несколько основных потоков (Flow 2). Редактирование заблокировано до разрешения неоднозначности.',
          'conflict'
        );
        return;
      }

      // Exactly 1 Flow 2 candidate resolved
      currentFlow2Stream = flow2Candidates[0];
      currentStreamId = currentFlow2Stream.id;
      renderFlow2Form(currentFlow2Stream);
    } catch (err) {
      if (thisRequestId !== currentLoadRequestId || currentCampaignId !== campaignId) {
        return;
      }
      console.error('Failed to load streams:', err);
      if (simLoadingState) simLoadingState.classList.add('hidden');
      editorCard.classList.add('hidden');
      if (flow1Stub) flow1Stub.classList.add('hidden');
      showNoticeWithErrorRetry(err.message || 'Ошибка загрузки потоков кампании', () => {
        loadCampaignStreams(campaignId);
      });
    }
  }

  function renderFlow2Form(stream) {
    if (!stream) {
      renderEmptyState();
      return;
    }

    initialStreamSnapshot = JSON.parse(JSON.stringify(stream));
    // Loaded shares are preserved as-is; NO automatic recalculation on open
    formOffers = JSON.parse(JSON.stringify(stream.offers || []));
    isFormDirty = false;

    streamNameEl.textContent = stream.name;
    streamIdBadge.textContent = `Stream ID: ${stream.id}`;

    emptyState.classList.add('hidden');
    editorCard.classList.remove('hidden');

    renderOffersTable();
    updateFormDirtyState();
  }

  function renderOffersTable() {
    offersTbody.innerHTML = '';

    if (formOffers.length === 0) {
      offersTbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding: 24px; color: var(--text-secondary);">В потоке нет прикрепленных офферов</td></tr>`;
      updateSummaryStats();
      return;
    }

    const activeCount = formOffers.filter((o) => o.state === 'active').length;

    formOffers.forEach((off) => {
      const tr = document.createElement('tr');
      const isRemoved = off.state === 'removed';
      tr.className = `offer-row ${isRemoved ? 'row-removed' : ''}`;

      // Resolve full offer name from catalog or fallback to 'Оффер #ID'
      const catalogName = catalogOffersMap.get(off.offer_id);
      let displayName = catalogName;
      if (!displayName && off.name && !off.name.startsWith('Offer #')) {
        displayName = off.name;
      }
      if (!displayName) {
        displayName = `Оффер #${off.offer_id}`;
      }

      // Name & ID column
      const tdName = document.createElement('td');
      tdName.className = 'col-offer';

      const nameCell = document.createElement('div');
      nameCell.className = 'offer-name-cell';
      nameCell.textContent = displayName;

      const idSub = document.createElement('div');
      idSub.className = 'offer-id-sub';
      idSub.textContent = `Offer ID: ${off.offer_id}`;

      tdName.appendChild(nameCell);
      tdName.appendChild(idSub);

      // Share column: read-only text (no inputs)
      const tdShare = document.createElement('td');
      tdShare.className = 'col-share';
      const shareSpan = document.createElement('span');
      shareSpan.className = 'share-text';
      if (isRemoved) {
        shareSpan.style.color = 'var(--text-secondary)';
        shareSpan.textContent = '0%';
      } else {
        shareSpan.textContent = `${off.share}%`;
      }
      tdShare.appendChild(shareSpan);

      // Status column
      const tdStatus = document.createElement('td');
      tdStatus.className = 'col-status';
      const badgeSpan = document.createElement('span');
      if (isRemoved) {
        badgeSpan.className = 'badge badge-removed';
        badgeSpan.textContent = 'Removed';
      } else {
        badgeSpan.className = 'badge badge-active';
        badgeSpan.textContent = 'Активен';
      }
      tdStatus.appendChild(badgeSpan);

      // Actions column: Remove button (disabled if <= 1 active offer)
      const tdActions = document.createElement('td');
      tdActions.className = 'col-actions';
      tdActions.style.textAlign = 'right';

      const removeBtn = document.createElement('button');
      removeBtn.type = 'button';
      removeBtn.className = 'btn btn-secondary btn-sm';
      removeBtn.textContent = 'Убрать из потока';
      removeBtn.disabled = activeCount <= 1;
      removeBtn.addEventListener('click', () => {
        removeOfferFromForm(off.offer_id);
      });
      tdActions.appendChild(removeBtn);

      tr.appendChild(tdName);
      tr.appendChild(tdShare);
      tr.appendChild(tdStatus);
      tr.appendChild(tdActions);
      offersTbody.appendChild(tr);
    });

    updateSummaryStats();
  }

  function removeOfferFromForm(offerId) {
    const activeOffers = formOffers.filter((o) => o.state === 'active');
    if (activeOffers.length <= 1) {
      showToast('Нельзя убрать единственный активный оффер из потока', 'warning');
      return;
    }

    formOffers = formOffers.filter((o) => o.offer_id !== offerId);
    rebalanceActiveShares();
    isFormDirty = true;
    updateFormDirtyState();
    renderOffersTable();
  }

  function addOfferToForm(offer) {
    if (formOffers.some((o) => o.offer_id === offer.id)) {
      showToast(`Оффер #${offer.id} уже находится в потоке`, 'warning');
      return;
    }

    if (offer.name) {
      catalogOffersMap.set(offer.id, offer.name);
    }

    formOffers.push({
      offer_id: offer.id,
      name: offer.name,
      share: 0,
      state: 'active',
    });

    rebalanceActiveShares();
    isFormDirty = true;
    updateFormDirtyState();
    renderOffersTable();

    if (offerCombobox) {
      offerCombobox.reset();
    }
  }

  // Fair deterministic distribution (Task 3.1 §2: 3 -> 34/33/33, 4 -> 25/25/25/25, 5 -> 20/20/20/20/20)
  function rebalanceActiveShares() {
    const active = formOffers.filter((o) => o.state === 'active');
    const n = active.length;
    if (n === 0) return;

    const base = Math.floor(100 / n);
    const rem = 100 % n;

    active.forEach((off, idx) => {
      off.share = base + (idx < rem ? 1 : 0);
    });
  }

  // Summary stats strictly for Flow 2 (under header stream name)
  function updateSummaryStats() {
    const active = formOffers.filter((o) => o.state === 'active');
    const totalShare = active.reduce((sum, o) => sum + (o.share || 0), 0);

    if (activeCountEl) {
      activeCountEl.textContent = `Активных: ${active.length}`;
    }
    if (totalShareEl) {
      totalShareEl.textContent = `${totalShare}%`;
    }

    const isValid = totalShare === 100 && active.length > 0;
    if (isValid) {
      if (totalShareEl) totalShareEl.className = 'share-valid';
      saveBtn.disabled = !isFormDirty;
    } else {
      if (totalShareEl) totalShareEl.className = 'share-invalid';
      saveBtn.disabled = true;
    }
  }

  function updateFormDirtyState() {
    if (isFormDirty) {
      unsavedBadge.classList.remove('hidden');
      revertBtn.disabled = false;
    } else {
      unsavedBadge.classList.add('hidden');
      revertBtn.disabled = true;
    }
    updateSummaryStats();
  }

  function renderEmptyState() {
    editorCard.classList.add('hidden');
    if (flow1Stub) flow1Stub.classList.add('hidden');
    if (simLoadingState) simLoadingState.classList.add('hidden');
    emptyState.classList.remove('hidden');
  }

  // Add offer button click handler
  addOfferBtn.addEventListener('click', () => {
    if (offerCombobox && offerCombobox.selectedOffer) {
      addOfferToForm(offerCombobox.selectedOffer);
    }
  });

  // Revert button
  revertBtn.addEventListener('click', () => {
    if (initialStreamSnapshot) {
      formOffers = JSON.parse(JSON.stringify(initialStreamSnapshot.offers || []));
      isFormDirty = false;
      clearNotice();
      updateFormDirtyState();
      renderOffersTable();
      if (offerCombobox) offerCombobox.reset();
      showToast('Изменения формы сброшены к исходному состоянию симулятора', 'info');
    }
  });

  // Save to simulator
  saveBtn.addEventListener('click', async () => {
    if (!currentCampaignId || !currentStreamId) return;

    const saveCampaignId = currentCampaignId;
    const saveStreamId = currentStreamId;
    saveBtn.disabled = true;
    saveSpinner.classList.remove('hidden');
    clearNotice();

    try {
      const payload = {
        offers: formOffers,
        expected_fingerprint: initialStreamSnapshot ? initialStreamSnapshot.fingerprint : null,
      };

      const res = await safeFetch(
        `/api/demo/simulator/campaigns/${saveCampaignId}/streams/${saveStreamId}/offers`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        }
      );

      const data = await res.json();
      // Guard against race condition if user switched campaigns while save request was in flight
      if (saveCampaignId !== currentCampaignId || saveStreamId !== currentStreamId) {
        console.warn('Discarding late Save response because campaign/stream changed.');
        return;
      }
      if (!res.ok) {
        if (res.status === 409) {
          showNotice(
            data.detail ||
              'Конфликт: состояние потока в симуляторе изменилось другим действием. Форма сохранена для разрешения.',
            'conflict'
          );
        }
        throw new Error(data.detail || 'Ошибка сохранения в симуляторе');
      }

      // Success: update snapshot with confirmed read-back response
      initialStreamSnapshot = JSON.parse(JSON.stringify(data));
      formOffers = JSON.parse(JSON.stringify(data.offers || []));
      isFormDirty = false;
      updateFormDirtyState();
      renderOffersTable();

      showNotice(
        'Изменения сохранены в симуляторе Keitaro. Перейдите в редактор ADROBOT и нажмите Fetch Streams.',
        'success'
      );
      showToast('Изменения сохранены в симуляторе!', 'success');
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      saveSpinner.classList.add('hidden');
      updateSummaryStats();
    }
  });

  // Switch campaign select
  campaignSelect.addEventListener('change', async () => {
    const val = campaignSelect.value;
    if (isFormDirty) {
      const ok = confirm('Есть несохранённые изменения в текущей форме. Уйти без сохранения?');
      if (!ok) {
        campaignSelect.value = currentCampaignId;
        return;
      }
    }

    if (val) {
      currentCampaignId = parseInt(val, 10);
      localStorage.setItem('adrobot_selected_campaign_demo', currentCampaignId);
      const newUrl = new URL(window.location);
      newUrl.searchParams.set('campaign_id', currentCampaignId);
      window.history.replaceState({}, '', newUrl);
      await loadCampaignStreams(currentCampaignId);
    } else {
      currentCampaignId = null;
      renderEmptyState();
    }
  });

  // Go to ADROBOT button
  goToAdrobotBtn.addEventListener('click', () => {
    if (isFormDirty) {
      const ok = confirm('Есть несохранённые изменения формы симулятора. Переключиться в ADROBOT?');
      if (!ok) return;
    }

    if (currentCampaignId) {
      localStorage.setItem('adrobot_selected_campaign_demo', currentCampaignId);
      window.location.href = `/demo/editor?campaign_id=${currentCampaignId}`;
    } else {
      window.location.href = '/demo/editor';
    }
  });

  function showNotice(msg, type = 'info') {
    simNotice.className = `editor-notice notice-${type}`;
    simNotice.textContent = ['conflict', 'error', 'warning'].includes(type) ? window.humanizeError(msg) : msg;
    simNotice.classList.remove('hidden');
  }

  function showNoticeWithErrorRetry(msg, retryFn) {
    simNotice.className = 'editor-notice notice-conflict';
    simNotice.innerHTML = '';

    const container = document.createElement('div');
    container.style.display = 'flex';
    container.style.alignItems = 'center';
    container.style.justifyContent = 'space-between';
    container.style.gap = '12px';
    container.style.flexWrap = 'wrap';

    const textSpan = document.createElement('span');
    textSpan.textContent = window.humanizeError(msg);

    const retryBtn = document.createElement('button');
    retryBtn.type = 'button';
    retryBtn.className = 'btn btn-secondary btn-sm';
    retryBtn.textContent = 'Повторить попытку';
    retryBtn.addEventListener('click', () => {
      clearNotice();
      retryFn();
    });

    container.appendChild(textSpan);
    container.appendChild(retryBtn);
    simNotice.appendChild(container);
    simNotice.classList.remove('hidden');
  }

  function clearNotice() {
    simNotice.className = 'editor-notice hidden';
    simNotice.innerHTML = '';
  }

  window.addEventListener('beforeunload', (e) => {
    if (isFormDirty) {
      e.preventDefault();
      e.returnValue = '';
    }
  });

  // Guard sidebar navigation links when dirty
  document.querySelectorAll('a.nav-item, a.sidebar-link').forEach((link) => {
    link.addEventListener('click', (e) => {
      if (isFormDirty) {
        const ok = confirm('Есть несохранённые изменения в форме симулятора. Переход приведёт к их потере. Продолжить?');
        if (!ok) {
          e.preventDefault();
        }
      }
    });
  });

  // Expose state so connection_mode.js can check dirty state before switching modes
  window.SIMULATOR_STATE = {
    get isDirty() {
      return isFormDirty;
    },
  };

  loadCampaigns();
});
