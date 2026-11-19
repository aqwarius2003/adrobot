document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('creatorForm');
  const nameInput = document.getElementById('campaignName');
  const namePreview = document.getElementById('namePreview');

  // GEO elements
  const geoPickerBox = document.getElementById('geoPickerBox');
  const geoTagsContainer = document.getElementById('geoTagsContainer');
  const geoSearchInput = document.getElementById('geoSearchInput');
  const geoDropdown = document.getElementById('geoDropdown');
  const geoInputHidden = document.getElementById('campaignGeo');
  const geoError = document.getElementById('geoError');

  // Routing preview elements
  const routingPreviewBox = document.getElementById('routingPreviewBox');
  const previewGeoContainer = document.getElementById('previewGeoContainer');
  const previewOfferText = document.getElementById('previewOfferText');

  // Offer Combobox elements
  const offerSearchInput = document.getElementById('offerSearchInput');
  const selectedOfferIdInput = document.getElementById('selectedOfferId');
  const offerDropdown = document.getElementById('offerDropdown');
  const selectedOffersContainer = document.getElementById('selectedOffersContainer');

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

  // Surname prefix for live name preview
  let surnamePrefix = 'test_novikov_';
  if (namePreview && namePreview.textContent) {
    const match = namePreview.textContent.match(/^(test_[a-z0-9_]+_)/i);
    if (match) surnamePrefix = match[1].toLowerCase();
  }

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
      const res = await fetch('/api/geo/countries');
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
      const res = await fetch('/api/options');
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
  // Name live preview
  // =========================================================================
  nameInput.addEventListener('input', () => {
    const raw = nameInput.value.trim();
    if (!raw) {
      namePreview.textContent = `${surnamePrefix}...`;
    } else if (raw.toLowerCase().startsWith(surnamePrefix)) {
      namePreview.textContent = raw;
    } else {
      namePreview.textContent = `${surnamePrefix}${raw}`;
    }
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

  function updateRoutingPreview() {
    if (!previewGeoContainer) return;

    if (selectedCodes.length === 0) {
      previewGeoContainer.innerHTML = '<strong id="previewGeoText">Страны не выбраны</strong>';
      return;
    }

    const itemsHtml = selectedCodes
      .map((code) => {
        const item = countryMap.get(code);
        const nameRu = item ? item.name_ru : code;
        const tooltip = `${nameRu} — ${code}`;
        return `<span class="preview-geo-code" tabindex="0" role="button" aria-label="${escapeHtml(nameRu)} (${code})" data-tooltip="${escapeHtml(tooltip)}">${code}</span>`;
      })
      .join(', ');

    previewGeoContainer.innerHTML = `${itemsHtml} &rarr; <code>https://google.com/</code>`;
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
    updateRoutingPreview();
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

  // =========================================================================
  // Offer Combobox Search & Multi-Select
  // =========================================================================
  let selectedOffers = []; // Array of { id: number, name: string }
  let offerDebounceTimeout = null;

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
    if (!selectedOffersContainer) return;
    selectedOffersContainer.innerHTML = '';
    const shares = calculateOfferShares(selectedOffers.length);

    selectedOffers.forEach((off, idx) => {
      const share = shares[idx];
      const tag = document.createElement('div');
      tag.className = 'selected-offer-tag';
      tag.innerHTML = `
        <span class="selected-offer-title">[ID: ${off.id}] ${escapeHtml(off.name)}</span>
        <span class="selected-offer-share">${share}%</span>
        <button type="button" class="btn-offer-remove" data-id="${off.id}" aria-label="Удалить оффер">&times;</button>
      `;

      tag.querySelector('.btn-offer-remove').addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        removeOffer(off.id);
      });

      selectedOffersContainer.appendChild(tag);
    });

    if (selectedOffers.length > 0) {
      selectedOfferIdInput.value = selectedOffers.map((o) => o.id).join(',');
      document.getElementById('offerError').classList.remove('visible');

      if (selectedOffers.length === 1) {
        previewOfferText.textContent = `[ID: ${selectedOffers[0].id}] ${selectedOffers[0].name} (100%)`;
      } else {
        const parts = selectedOffers.map((o, idx) => `[ID: ${o.id}] ${o.name} (${shares[idx]}%)`);
        previewOfferText.textContent = parts.join(', ');
      }
    } else {
      selectedOfferIdInput.value = '';
      previewOfferText.textContent = 'Оффер не выбран';
    }
  }

  function selectOffer(id, name) {
    const numId = parseInt(id, 10);
    if (!numId) return;
    if (!selectedOffers.some((o) => o.id === numId)) {
      selectedOffers.push({ id: numId, name: name });
      renderSelectedOffers();
    }
    offerSearchInput.value = '';
    offerDropdown.classList.add('hidden');
    offerSearchInput.focus();
  }

  function removeOffer(id) {
    const numId = parseInt(id, 10);
    selectedOffers = selectedOffers.filter((o) => o.id !== numId);
    renderSelectedOffers();
  }

  offerSearchInput.addEventListener('input', () => {
    clearTimeout(offerDebounceTimeout);
    const query = offerSearchInput.value.trim();
    if (query.length < 1) {
      offerDropdown.classList.add('hidden');
      return;
    }
    offerDebounceTimeout = setTimeout(async () => {
      try {
        const res = await fetch(`/api/offers?search=${encodeURIComponent(query)}`);
        if (!res.ok) throw new Error('Search failed');
        const offers = await res.json();
        renderOfferDropdown(offers);
      } catch (err) {
        console.error('Offer search error:', err);
      }
    }, 250);
  });

  function renderOfferDropdown(offers) {
    if (!offers || offers.length === 0) {
      offerDropdown.innerHTML = '<div class="dropdown-item">Ничего не найдено</div>';
      offerDropdown.classList.remove('hidden');
      return;
    }
    offerDropdown.innerHTML = offers
      .map(
        (o) =>
          `<div class="dropdown-item" data-id="${o.id}" data-name="${escapeHtml(o.name)}">[ID: ${o.id}] ${escapeHtml(o.name)}</div>`
      )
      .join('');
    offerDropdown.classList.remove('hidden');

    offerDropdown.querySelectorAll('.dropdown-item').forEach((el) => {
      el.addEventListener('click', () => {
        const id = el.getAttribute('data-id');
        const name = el.getAttribute('data-name');
        if (id) {
          selectOffer(id, name);
        }
      });
    });
  }

  document.addEventListener('click', (e) => {
    if (!offerSearchInput.contains(e.target) && !offerDropdown.contains(e.target)) {
      offerDropdown.classList.add('hidden');
    }
  });

  // =========================================================================
  // Form submission
  // =========================================================================
  form.addEventListener('submit', async (e) => {
    e.preventDefault();

    // Reset errors
    document.getElementById('nameError').classList.remove('visible');
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
    submitBtnText.textContent = 'Создание в Keitaro...';

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
      const res = await fetch('/api/campaigns', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Ошибка создания кампании');
      }

      // Display result
      routingPreviewBox.classList.add('hidden');
      creationResultBox.classList.remove('hidden');

      resultCampaignId.textContent = data.remote_campaign_id;
      resultCampaignName.textContent = data.name;
      resultTrackingUrl.value = data.tracking_url || 'Не сформирован';
      resultFlowIds.textContent = `Flow 1: ${data.flow1_stream_id || '-'}, Flow 2: ${data.flow2_stream_id || '-'}`;
      resultVerificationStatus.textContent =
        data.verification_status === 'verified' ? 'Подтверждено KT' : data.verification_status;
      goToEditorLink.href = `/editor?campaign_id=${data.remote_campaign_id}`;

      showToast('Кампания успешно создана и верифицирована в Keitaro!', 'success');
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
    routingPreviewBox.classList.remove('hidden');
    form.reset();

    // Reset GEO picker
    selectedCodes = [];
    renderSelectedTags();
    geoSearchInput.value = '';
    hideGeoDropdown();
    geoError.classList.remove('visible');

    // Reset Offers
    selectedOffers = [];
    renderSelectedOffers();
    document.getElementById('offerError').classList.remove('visible');

    // Reset previews
    namePreview.textContent = `${surnamePrefix}...`;
    previewOfferText.textContent = 'Оффер не выбран';
  });
});
