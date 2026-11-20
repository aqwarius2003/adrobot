document.addEventListener('DOMContentLoaded', () => {
  const config = window.APP_CONFIG || {};
  const currentMode = config.is_demo ? 'demo' : 'real';
  const apiPrefix = `/api/${currentMode}`;
  const safeFetch = (window.apiClient && window.apiClient.fetch) || fetch;
  const form = document.getElementById('creatorForm');
  const nameInput = document.getElementById('campaignName');

  // GEO elements
  const geoPickerBox = document.getElementById('geoPickerBox');
  const geoTagsContainer = document.getElementById('geoTagsContainer');
  const geoSearchInput = document.getElementById('geoSearchInput');
  const geoDropdown = document.getElementById('geoDropdown');
  const geoInputHidden = document.getElementById('campaignGeo');
  const geoError = document.getElementById('geoError');

  // Single selected-offer list inside Flow 2
  const previewOfferText = document.getElementById('previewOfferText');
  const previewOfferUndo = document.getElementById('previewOfferUndo');
  const previewRemovedOfferText = document.getElementById('previewRemovedOfferText');
  const undoPreviewOfferBtn = document.getElementById('undoPreviewOfferBtn');

  // Offer Combobox elements
  const offerSearchInput = document.getElementById('offerSearchInput');
  const selectedOfferIdInput = document.getElementById('selectedOfferId');
  const offerDropdown = document.getElementById('offerDropdown');
  const offerSummary = document.getElementById('creatorOfferSummary');

  // Settings selects
  const domainSelect = document.getElementById('domainSelect');
  const groupSelect = document.getElementById('groupSelect');
  const sourceSelect = document.getElementById('sourceSelect');

  // Action buttons & results
  const submitBtn = document.getElementById('submitCreateBtn');
  const submitSpinner = document.getElementById('submitSpinner');
  const submitBtnText = document.getElementById('submitBtnText');

  const creationResultBox = document.getElementById('creationResultBox');
  const resultCampaignId = document.getElementById('resultCampaignId');
  const resultCampaignName = document.getElementById('resultCampaignName');
  const resultTrackingUrl = document.getElementById('resultTrackingUrl');
  const resultFlowIds = document.getElementById('resultFlowIds');
  const resultVerificationStatus = document.getElementById('resultVerificationStatus');
  const copyUrlBtn = document.getElementById('copyUrlBtn');
  const goToEditorLink = document.getElementById('goToEditorLink');
  const createAnotherBtn = document.getElementById('createAnotherBtn');

  // =========================================================================
  // Country Catalog & State
  // =========================================================================
  let countriesCatalog = [];
  const countryMap = new Map();
  let selectedCodes = []; // Array of selected 2-letter uppercase codes
  let activeDropdownItems = [];
  let highlightedIndex = -1;

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function normalizeText(str) {
    return (str || '').toLowerCase().replace(/ё/g, 'е').trim();
  }

  // Load country catalog from backend
  async function loadCountryCatalog() {
    try {
      geoError.classList.remove('visible');
      geoError.innerHTML = '';
      const res = await safeFetch(`${apiPrefix}/geo/countries`);
      if (!res.ok) throw new Error('Не удалось загрузить справочник стран');
      countriesCatalog = await res.json();
      countryMap.clear();
      countriesCatalog.forEach((item) => {
        countryMap.set(item.code.toUpperCase(), item);
      });
    } catch (err) {
      console.error('Countries catalog load error:', err);
      geoError.innerHTML =
        'Ошибка загрузки справочника стран. <button type="button" class="btn btn-secondary btn-sm geo-retry-btn" id="retryCountriesBtn">Повторить</button>';
      geoError.classList.add('visible');
      const retryBtn = document.getElementById('retryCountriesBtn');
      if (retryBtn) {
        retryBtn.addEventListener('click', () => loadCountryCatalog());
      }
    }
  }
  loadCountryCatalog();

  // Load select options (domains, groups, sources)
  async function loadOptions() {
    try {
      const res = await safeFetch(`${apiPrefix}/options`);
      if (!res.ok) throw new Error('Options fetch failed');
      const data = await res.json();

      domainSelect.innerHTML = '<option value="">-- Выберите домен --</option>';
      data.domains.forEach((d) => {
        domainSelect.innerHTML += `<option value="${d.id}">${escapeHtml(d.name)}</option>`;
      });

      groupSelect.innerHTML = '<option value="">-- Выберите группу --</option>';
      data.groups.forEach((g) => {
        groupSelect.innerHTML += `<option value="${g.id}">${escapeHtml(g.name)}</option>`;
      });

      sourceSelect.innerHTML = '<option value="">-- Выберите источник --</option>';
      data.traffic_sources.forEach((s) => {
        sourceSelect.innerHTML += `<option value="${s.id}">${escapeHtml(s.name)}</option>`;
      });
    } catch (err) {
      console.warn('Could not load lookup options:', err);
    }
  }
  loadOptions();

  // =========================================================================
  // Clear the name validation message when the user edits it.
  // =========================================================================
  nameInput.addEventListener('input', () => {
    document.getElementById('nameError').classList.remove('visible');
    nameInput.removeAttribute('aria-invalid');
  });

  // =========================================================================
  // GEO Country Picker Implementation
  // =========================================================================

  function updateHiddenGeoInput() {
    geoInputHidden.value = selectedCodes.join(',');
    if (selectedCodes.length > 0) {
      geoError.classList.remove('visible');
    }
  }

  function renderSelectedTags() {
    geoTagsContainer.innerHTML = '';
    selectedCodes.forEach((code, idx) => {
      const item = countryMap.get(code);
      const nameRu = item ? item.name_ru : code;
      const tooltip = `${nameRu} — ${code}`;
      const removeLabel = `Убрать ${nameRu}`;

      const tag = document.createElement('span');
      tag.className = 'geo-tag';
      tag.dataset.code = code;

      tag.innerHTML = `
        <span 
          class="geo-tag-code" 
          tabindex="0" 
          role="button" 
          aria-label="${escapeHtml(nameRu)} (${code})" 
          data-tooltip="${escapeHtml(tooltip)}"
        >${code}</span>
        <button 
          type="button" 
          class="geo-tag-remove" 
          aria-label="${escapeHtml(removeLabel)}" 
          data-code="${code}"
          data-index="${idx}"
        >&times;</button>
      `;

      // Remove button listener
      const removeBtn = tag.querySelector('.geo-tag-remove');
      removeBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        removeCountry(code);
      });

      geoTagsContainer.appendChild(tag);
    });

    updateHiddenGeoInput();
  }

  function addCountry(code) {
    const cleanCode = code.toUpperCase();
    if (!cleanCode || selectedCodes.includes(cleanCode)) return;
    if (!countryMap.has(cleanCode)) return;

    selectedCodes.push(cleanCode);
    renderSelectedTags();

    geoSearchInput.value = '';
    hideGeoDropdown();
    geoSearchInput.focus();
  }

  function removeCountry(code) {
    const cleanCode = code.toUpperCase();
    const idx = selectedCodes.indexOf(cleanCode);
    if (idx === -1) return;

    selectedCodes.splice(idx, 1);
    renderSelectedTags();

    // Hide any active tooltip for the removed element
    if (window.AppTooltip && typeof window.AppTooltip.hide === 'function') {
      window.AppTooltip.hide();
    }

    // Maintain keyboard focus
    const remainingRemoveBtns = geoTagsContainer.querySelectorAll('.geo-tag-remove');
    if (remainingRemoveBtns.length > 0) {
      const nextIdx = Math.min(idx, remainingRemoveBtns.length - 1);
      remainingRemoveBtns[nextIdx].focus();
    } else {
      geoSearchInput.focus();
    }
  }

  // Clicking on picker box focuses the input
  geoPickerBox.addEventListener('click', (e) => {
    if (!e.target.closest('.geo-tag-remove') && !e.target.closest('.geo-tag-code')) {
      geoSearchInput.focus();
    }
  });

  // Search & Filter ranking algorithm
  function filterCountries(rawQuery) {
    const q = normalizeText(rawQuery);
    if (!q) return [];

    const bucketExactCode = [];
    const bucketExactAliasOrName = [];
    const bucketNameRuStartsWith = [];
    const bucketWordOrAliasStartsWith = [];
    const bucketSubstr = [];

    for (const item of countriesCatalog) {
      const codeLower = item.code.toLowerCase();
      const nameRuNorm = normalizeText(item.name_ru);
      const nameEnNorm = (item.name_en || '').toLowerCase();
      const aliasesNorm = (item.aliases || []).map(normalizeText);

      // 1. Exact ISO code
      if (codeLower === q) {
        bucketExactCode.push(item);
        continue;
      }

      // 2. Exact alias or name
      if (q === nameRuNorm || q === nameEnNorm || aliasesNorm.includes(q)) {
        bucketExactAliasOrName.push(item);
        continue;
      }

      // 3. Name_ru starts with query
      if (nameRuNorm.startsWith(q)) {
        bucketNameRuStartsWith.push(item);
        continue;
      }

      // 4. Word in name starts with query, alias starts with query, name_en starts with query, or code starts with query
      const wordsRu = nameRuNorm.split(/[\s\-_—,()]+/);
      const wordsEn = nameEnNorm.split(/[\s\-_—,()]+/);
      if (
        wordsRu.some((w) => w && w.startsWith(q)) ||
        aliasesNorm.some((a) => a && a.startsWith(q)) ||
        nameEnNorm.startsWith(q) ||
        wordsEn.some((w) => w && w.startsWith(q)) ||
        codeLower.startsWith(q)
      ) {
        bucketWordOrAliasStartsWith.push(item);
        continue;
      }

      // 5. Substring match
      if (
        nameRuNorm.includes(q) ||
        nameEnNorm.includes(q) ||
        aliasesNorm.some((a) => a.includes(q)) ||
        codeLower.includes(q)
      ) {
        bucketSubstr.push(item);
      }
    }

    const sortRu = (a, b) => a.name_ru.localeCompare(b.name_ru, 'ru');
    bucketExactAliasOrName.sort(sortRu);
    bucketNameRuStartsWith.sort(sortRu);
    bucketWordOrAliasStartsWith.sort(sortRu);
    bucketSubstr.sort(sortRu);

    return [
      ...bucketExactCode,
      ...bucketExactAliasOrName,
      ...bucketNameRuStartsWith,
      ...bucketWordOrAliasStartsWith,
      ...bucketSubstr,
    ];
  }

  function highlightMatchedText(nameRu, rawQuery) {
    const q = normalizeText(rawQuery);
    if (!q) return escapeHtml(nameRu);

    const nameNorm = normalizeText(nameRu);
    const idx = nameNorm.indexOf(q);

    // If match was only in code or alias and not in name, do NOT highlight
    if (idx === -1) {
      return escapeHtml(nameRu);
    }

    const matchLen = q.length;
    const before = nameRu.slice(0, idx);
    const match = nameRu.slice(idx, idx + matchLen);
    const after = nameRu.slice(idx + matchLen);

    return `${escapeHtml(before)}<mark class="geo-match">${escapeHtml(match)}</mark>${escapeHtml(after)}`;
  }

  function renderGeoDropdown(items, query) {
    geoDropdown.innerHTML = '';
    activeDropdownItems = items;
    highlightedIndex = -1;

    if (!items || items.length === 0) {
      geoDropdown.innerHTML =
        '<div class="geo-empty-item">Страна не найдена. Попробуйте другое название или код</div>';
      showGeoDropdown();
      return;
    }

    items.forEach((item, idx) => {
      const isSelected = selectedCodes.includes(item.code);
      const highlightedName = highlightMatchedText(item.name_ru, query);

      const div = document.createElement('div');
      div.className = `geo-dropdown-item ${isSelected ? 'selected' : ''}`;
      div.setAttribute('role', 'option');
      div.setAttribute('id', `geo-opt-${item.code}`);
      div.setAttribute('aria-selected', isSelected ? 'true' : 'false');
      div.dataset.code = item.code;
      div.dataset.index = idx;

      div.innerHTML = `
        <div class="geo-item-left">
          <span class="geo-item-name">${highlightedName}</span>
          <span class="geo-item-sep">·</span>
          <span class="geo-item-code">${escapeHtml(item.code)}</span>
        </div>
        ${isSelected ? '<span class="geo-item-badge">Выбрано</span>' : ''}
      `;

      if (!isSelected) {
        div.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          addCountry(item.code);
        });
      }

      geoDropdown.appendChild(div);
    });

    // Auto-highlight first non-selected option
    const firstNonSelected = items.findIndex((i) => !selectedCodes.includes(i.code));
    if (firstNonSelected !== -1) {
      setHighlightedOption(firstNonSelected);
    }

    showGeoDropdown();
  }

  function setHighlightedOption(idx) {
    const options = geoDropdown.querySelectorAll('.geo-dropdown-item');
    options.forEach((opt) => opt.classList.remove('highlighted'));

    if (idx >= 0 && idx < options.length) {
      highlightedIndex = idx;
      const target = options[idx];
      target.classList.add('highlighted');
      geoSearchInput.setAttribute('aria-activedescendant', target.id);
      target.scrollIntoView({ block: 'nearest' });
    } else {
      highlightedIndex = -1;
      geoSearchInput.removeAttribute('aria-activedescendant');
    }
  }

  function showGeoDropdown() {
    geoDropdown.classList.remove('hidden');
    geoSearchInput.setAttribute('aria-expanded', 'true');
    positionGeoDropdown();
  }

  function positionGeoDropdown() {
    const box = geoPickerBox.getBoundingClientRect();
    const viewport = window.visualViewport;
    const top = viewport ? viewport.offsetTop : 0;
    const bottom = top + (viewport ? viewport.height : window.innerHeight);
    const below = bottom - box.bottom - 12;
    const above = box.top - top - 12;
    const dropup = below < 180 && above > below;
    geoDropdown.classList.toggle('dropup', dropup);
    geoDropdown.style.maxHeight = `${Math.max(0, Math.min(240, dropup ? above : below))}px`;
  }

  function hideGeoDropdown() {
    geoDropdown.classList.add('hidden');
    geoSearchInput.setAttribute('aria-expanded', 'false');
    geoSearchInput.removeAttribute('aria-activedescendant');
    highlightedIndex = -1;
  }

  // Search input typing
  geoSearchInput.addEventListener('input', () => {
    geoError.classList.remove('visible');
    const query = geoSearchInput.value.trim();
    if (query.length < 1) {
      hideGeoDropdown();
      return;
    }
    const filtered = filterCountries(query);
    renderGeoDropdown(filtered, query);
  });

  // Keyboard navigation on search input
  geoSearchInput.addEventListener('keydown', (e) => {
    const isDropdownVisible = !geoDropdown.classList.contains('hidden');

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (!isDropdownVisible) {
        const query = geoSearchInput.value.trim();
        if (query.length >= 1) {
          renderGeoDropdown(filterCountries(query), query);
        }
        return;
      }
      const total = activeDropdownItems.length;
      if (total === 0) return;
      let next = highlightedIndex + 1;
      while (next < total && selectedCodes.includes(activeDropdownItems[next].code)) {
        next++;
      }
      if (next < total) setHighlightedOption(next);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (!isDropdownVisible) return;
      let prev = highlightedIndex - 1;
      while (prev >= 0 && selectedCodes.includes(activeDropdownItems[prev].code)) {
        prev--;
      }
      if (prev >= 0) setHighlightedOption(prev);
    } else if (e.key === 'Enter') {
      // Prevent form submit while interacting with combobox/search
      e.preventDefault();
      e.stopPropagation();

      if (isDropdownVisible && highlightedIndex >= 0 && highlightedIndex < activeDropdownItems.length) {
        const selectedItem = activeDropdownItems[highlightedIndex];
        if (!selectedCodes.includes(selectedItem.code)) {
          addCountry(selectedItem.code);
        }
      } else if (geoSearchInput.value.trim()) {
        const query = geoSearchInput.value.trim();
        const results = filterCountries(query);
        const candidate = results.find((r) => !selectedCodes.includes(r.code));
        if (candidate) {
          addCountry(candidate.code);
        }
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      hideGeoDropdown();
      if (window.AppTooltip && typeof window.AppTooltip.hide === 'function') {
        window.AppTooltip.hide();
      }
    } else if (e.key === 'Tab') {
      hideGeoDropdown();
    } else if (e.key === 'Backspace' && !geoSearchInput.value && selectedCodes.length > 0) {
      // Backspace on empty search input removes last selected tag
      removeCountry(selectedCodes[selectedCodes.length - 1]);
    }
  });

  // Batch paste support: AU, RO, au
  geoSearchInput.addEventListener('paste', (e) => {
    const pasted = (e.clipboardData || window.clipboardData).getData('text');
    if (!pasted) return;

    // Check if pasted text looks like comma-separated or whitespace-separated codes
    if (pasted.includes(',') || /^[A-Za-z\s,]+$/.test(pasted)) {
      const tokens = pasted
        .split(/[,\s]+/)
        .map((t) => t.trim().toUpperCase())
        .filter(Boolean);

      // If all tokens are 2-letter codes, process as a batch
      const allAreCodes = tokens.length > 0 && tokens.every((t) => /^[A-Z]{2}$/.test(t));
      if (allAreCodes) {
        e.preventDefault();

        // Validate all codes before adding
        const invalidCodes = tokens.filter((code) => !countryMap.has(code));
        if (invalidCodes.length > 0) {
          geoError.textContent = `Неизвестный код страны: "${invalidCodes[0]}". Пакет кодов не добавлен.`;
          geoError.classList.add('visible');
          return;
        }

        // Add valid codes preserving order, skipping duplicates
        geoError.classList.remove('visible');
        tokens.forEach((code) => {
          if (!selectedCodes.includes(code)) {
            selectedCodes.push(code);
          }
        });

        renderSelectedTags();
        geoSearchInput.value = '';
        hideGeoDropdown();
        return;
      }
    }
    // Otherwise let default paste behavior happen
  });

  // Close dropdown on outside click
  document.addEventListener('click', (e) => {
    if (!geoPickerBox.contains(e.target) && !geoDropdown.contains(e.target)) {
      hideGeoDropdown();
    }
  });
  const repositionGeo = () => {
    if (!geoDropdown.classList.contains('hidden')) positionGeoDropdown();
  };
  window.addEventListener('resize', repositionGeo);
  document.querySelector('.page-content').addEventListener('scroll', repositionGeo, { passive: true });
  window.visualViewport?.addEventListener('resize', repositionGeo);

  // =========================================================================
  // Offer Combobox Search & Multi-Select
  // =========================================================================
  let selectedOffers = []; // Array of { id: number, name: string }
  let lastRemovedOffer = null;

  function calculateOfferShares(count) {
    if (count <= 0) return [];
    if (count === 1) return [100];
    const q = Math.floor(100 / count);
    const r = 100 % count;
    const shares = [];
    for (let i = 0; i < count; i++) {
      shares.push(q + (i < r ? 1 : 0));
    }
    return shares;
  }

  function renderSelectedOffers() {
    const shares = calculateOfferShares(selectedOffers.length);
    previewOfferText.replaceChildren();
    if (selectedOffers.length > 0) {
      selectedOfferIdInput.value = selectedOffers.map((o) => o.id).join(',');
      document.getElementById('offerError').classList.remove('visible');

      selectedOffers.forEach((offer, index) => {
        const row = document.createElement('div');
        row.className = 'creator-preview-offer';
        row.setAttribute('role', 'listitem');

        const details = document.createElement('div');
        const name = document.createElement('span');
        name.className = 'creator-preview-offer-name';
        name.textContent = offer.name;
        const id = document.createElement('small');
        id.className = 'creator-preview-offer-id';
        id.textContent = `ID: ${offer.id}`;
        details.append(name, id);

        const share = document.createElement('strong');
        share.className = 'creator-preview-offer-share';
        share.textContent = `${shares[index]}%`;

        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'btn btn-secondary btn-sm';
        remove.textContent = 'Убрать';
        remove.setAttribute('aria-label', `Убрать из формы ${offer.name}`);
        remove.addEventListener('click', () => removeOffer(offer.id));
        row.append(details, share, remove);
        previewOfferText.appendChild(row);
      });
    } else {
      selectedOfferIdInput.value = '';
      previewOfferText.textContent = 'Офферы не выбраны';
    }
    offerSummary.textContent = `Активных: ${selectedOffers.length} · Итого: ${shares.reduce((sum, share) => sum + share, 0)}%`;
    previewOfferUndo.classList.toggle('hidden', !lastRemovedOffer);
    previewRemovedOfferText.textContent = lastRemovedOffer
      ? `Убран из формы: ${lastRemovedOffer.offer.name}`
      : '';
  }

  function selectOffer(id, name) {
    const numId = parseInt(id, 10);
    if (!numId) return;
    if (!selectedOffers.some((o) => o.id === numId)) {
      if (lastRemovedOffer && lastRemovedOffer.offer.id === numId) lastRemovedOffer = null;
      selectedOffers.push({ id: numId, name: name });
      renderSelectedOffers();
    }
    offerCombobox.reset();
    offerSearchInput.focus();
  }

  function removeOffer(id) {
    const numId = parseInt(id, 10);
    const position = selectedOffers.findIndex((offer) => offer.id === numId);
    if (position < 0) return;
    lastRemovedOffer = { offer: selectedOffers[position], position };
    selectedOffers.splice(position, 1);
    renderSelectedOffers();
    undoPreviewOfferBtn.focus();
  }

  undoPreviewOfferBtn.addEventListener('click', () => {
    if (!lastRemovedOffer) return;
    const { offer, position } = lastRemovedOffer;
    lastRemovedOffer = null;
    selectedOffers.splice(position, 0, offer);
    renderSelectedOffers();
    offerSearchInput.focus();
  });

  const offerCombobox = new OfferCombobox({
    input: offerSearchInput,
    dropdown: offerDropdown,
    cardElement: form.closest('.card'),
    getExcludedOfferIds: () => new Set(selectedOffers.map((offer) => offer.id)),
    searchFn: async (query) => {
      const res = await safeFetch(`${apiPrefix}/offers?search=${encodeURIComponent(query)}`);
      if (!res.ok) throw new Error('Не удалось загрузить офферы');
      return res.json();
    },
    onSelect: (offer) => selectOffer(offer.id, offer.name),
  });
  // Enter belongs to the combobox even when results are empty or still loading.
  offerSearchInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') event.preventDefault();
  });

  // =========================================================================
  // Form submission
  // =========================================================================
  form.addEventListener('submit', async (e) => {
    e.preventDefault();

    // Reset errors
    document.getElementById('nameError').classList.remove('visible');
    nameInput.removeAttribute('aria-invalid');
    geoError.classList.remove('visible');
    document.getElementById('offerError').classList.remove('visible');

    let hasErrors = false;
    const name = nameInput.value.trim();
    if (!name) {
      const err = document.getElementById('nameError');
      err.textContent = 'Укажите название кампании';
      err.classList.add('visible');
      hasErrors = true;
    }

    // Unconfirmed search text check
    const unconfirmedSearch = geoSearchInput.value.trim();
    if (unconfirmedSearch) {
      geoError.textContent = 'Выберите страну из списка или очистите поиск';
      geoError.classList.add('visible');
      geoSearchInput.focus();
      hasErrors = true;
    } else if (selectedCodes.length === 0) {
      geoError.textContent = 'Выберите хотя бы одну страну';
      geoError.classList.add('visible');
      geoSearchInput.focus();
      hasErrors = true;
    }

    if (selectedOffers.length === 0) {
      const err = document.getElementById('offerError');
      err.textContent = 'Выберите хотя бы один оффер из списка';
      err.classList.add('visible');
      hasErrors = true;
    }

    if (hasErrors) return;

    // Send creation request
    submitBtn.disabled = true;
    submitSpinner.classList.remove('hidden');
    submitBtnText.textContent = config.is_demo ? 'Создание в симуляторе...' : 'Создание в Keitaro...';

    const payload = {
      name: name,
      geo: selectedCodes.join(','),
      offer_ids: selectedOffers.map((o) => o.id),
      offer_id: selectedOffers[0].id,
      domain_id: domainSelect.value ? parseInt(domainSelect.value, 10) : null,
      group_id: groupSelect.value ? groupSelect.value : null,
      traffic_source_id: sourceSelect.value ? parseInt(sourceSelect.value, 10) : null,
    };

    try {
      const res = await safeFetch(`${apiPrefix}/campaigns`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) {
        if (res.status === 409 && data.code === 'campaign_name_exists') {
          const error = document.getElementById('nameError');
          error.textContent = data.detail;
          error.classList.add('visible');
          nameInput.setAttribute('aria-invalid', 'true');
          nameInput.focus();
          nameInput.select();
          return;
        }
        throw new Error(data.detail || 'Ошибка создания кампании');
      }

      // Display result
      form.classList.add('hidden');
      creationResultBox.classList.remove('hidden');

      resultCampaignId.textContent = data.remote_campaign_id;
      resultCampaignName.textContent = data.name;
      resultTrackingUrl.value = data.tracking_url || 'Не сформирован';
      resultFlowIds.textContent = `Flow 1: ${data.flow1_stream_id || '-'}, Flow 2: ${data.flow2_stream_id || '-'}`;
      const isDemo = window.APP_CONFIG && window.APP_CONFIG.is_demo;
      resultVerificationStatus.textContent =
        data.verification_status === 'verified'
          ? (isDemo ? 'Подтверждено в демо' : 'Подтверждено KT')
          : data.verification_status;
      goToEditorLink.href = `/${currentMode}/editor?campaign_id=${data.remote_campaign_id}`;
      creationResultBox.focus();

      showToast(
        isDemo
          ? 'Кампания успешно создана и верифицирована в демо-симуляторе!'
          : 'Кампания успешно создана и верифицирована в Keitaro!',
        'success'
      );
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      submitBtn.disabled = false;
      submitSpinner.classList.add('hidden');
      submitBtnText.textContent = 'Создать кампанию';
    }
  });

  // Copy URL button
  copyUrlBtn.addEventListener('click', () => {
    if (resultTrackingUrl.value) {
      navigator.clipboard.writeText(resultTrackingUrl.value);
      showToast('Tracking URL скопирован в буфер обмена', 'info');
    }
  });

  // Create another campaign button
  createAnotherBtn.addEventListener('click', () => {
    creationResultBox.classList.add('hidden');
    form.classList.remove('hidden');
    form.reset();

    // Reset GEO picker
    selectedCodes = [];
    renderSelectedTags();
    geoSearchInput.value = '';
    hideGeoDropdown();
    geoError.classList.remove('visible');

    // Reset Offers
    selectedOffers = [];
    lastRemovedOffer = null;
    offerCombobox.reset();
    renderSelectedOffers();
    document.getElementById('offerError').classList.remove('visible');

    document.getElementById('nameError').classList.remove('visible');
    nameInput.removeAttribute('aria-invalid');
    nameInput.focus();
  });
});
