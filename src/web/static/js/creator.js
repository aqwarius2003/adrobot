document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('creatorForm');
  const nameInput = document.getElementById('campaignName');
  const namePreview = document.getElementById('namePreview');
  const geoInput = document.getElementById('campaignGeo');
  const previewGeoText = document.getElementById('previewGeoText');
  const previewOfferText = document.getElementById('previewOfferText');

  const offerSearchInput = document.getElementById('offerSearchInput');
  const selectedOfferIdInput = document.getElementById('selectedOfferId');
  const offerDropdown = document.getElementById('offerDropdown');
  const selectedOfferBadge = document.getElementById('selectedOfferBadge');
  const selectedOfferText = document.getElementById('selectedOfferText');
  const clearOfferBtn = document.getElementById('clearOfferBtn');

  const domainSelect = document.getElementById('domainSelect');
  const groupSelect = document.getElementById('groupSelect');
  const sourceSelect = document.getElementById('sourceSelect');

  const submitBtn = document.getElementById('submitCreateBtn');
  const submitSpinner = document.getElementById('submitSpinner');
  const submitBtnText = document.getElementById('submitBtnText');

  const routingPreviewBox = document.getElementById('routingPreviewBox');
  const creationResultBox = document.getElementById('creationResultBox');
  const resultCampaignId = document.getElementById('resultCampaignId');
  const resultCampaignName = document.getElementById('resultCampaignName');
  const resultTrackingUrl = document.getElementById('resultTrackingUrl');
  const resultFlowIds = document.getElementById('resultFlowIds');
  const resultVerificationStatus = document.getElementById('resultVerificationStatus');
  const copyUrlBtn = document.getElementById('copyUrlBtn');
  const goToEditorLink = document.getElementById('goToEditorLink');
  const createAnotherBtn = document.getElementById('createAnotherBtn');

  // Load select options
  async function loadOptions() {
    try {
      const res = await fetch('/api/options');
      if (!res.ok) throw new Error('Options fetch failed');
      const data = await res.json();

      // Populate domains
      domainSelect.innerHTML = '<option value="">-- Выберите домен --</option>';
      data.domains.forEach(d => {
        domainSelect.innerHTML += `<option value="${d.id}">${d.name}</option>`;
      });

      // Populate groups
      groupSelect.innerHTML = '<option value="">-- Выберите группу --</option>';
      data.groups.forEach(g => {
        groupSelect.innerHTML += `<option value="${g.id}">${g.name}</option>`;
      });

      // Populate traffic sources
      sourceSelect.innerHTML = '<option value="">-- Выберите источник --</option>';
      data.traffic_sources.forEach(s => {
        sourceSelect.innerHTML += `<option value="${s.id}">${s.name}</option>`;
      });
    } catch (err) {
      console.warn('Could not load lookup options:', err);
    }
  }
  loadOptions();

  // Name input live preview
  nameInput.addEventListener('input', () => {
    const raw = nameInput.value.trim();
    if (!raw) {
      namePreview.textContent = 'test_novikov_...';
    } else if (raw.toLowerCase().startsWith('test_')) {
      namePreview.textContent = raw;
    } else {
      namePreview.textContent = `test_novikov_${raw}`;
    }
  });

  // GEO input live preview
  geoInput.addEventListener('input', () => {
    const raw = geoInput.value.trim().toUpperCase();
    previewGeoText.textContent = raw || 'не указаны';
  });

  // Offer Combobox Search
  let debounceTimeout = null;
  offerSearchInput.addEventListener('input', () => {
    clearTimeout(debounceTimeout);
    const query = offerSearchInput.value.trim();
    if (query.length < 1) {
      offerDropdown.classList.add('hidden');
      return;
    }
    debounceTimeout = setTimeout(async () => {
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
      .map(o => `<div class="dropdown-item" data-id="${o.id}" data-name="${o.name}">[ID: ${o.id}] ${o.name}</div>`)
      .join('');
    offerDropdown.classList.remove('hidden');

    offerDropdown.querySelectorAll('.dropdown-item').forEach(el => {
      el.addEventListener('click', () => {
        const id = el.getAttribute('data-id');
        const name = el.getAttribute('data-name');
        if (id) {
          selectOffer(id, name);
        }
      });
    });
  }

  function selectOffer(id, name) {
    selectedOfferIdInput.value = id;
    selectedOfferText.textContent = `[ID: ${id}] ${name}`;
    previewOfferText.textContent = `[ID: ${id}] ${name} (100%)`;
    selectedOfferBadge.classList.remove('hidden');
    offerSearchInput.value = '';
    offerDropdown.classList.add('hidden');
    document.getElementById('offerError').classList.remove('visible');
  }

  clearOfferBtn.addEventListener('click', () => {
    selectedOfferIdInput.value = '';
    selectedOfferBadge.classList.add('hidden');
    previewOfferText.textContent = 'Оффер не выбран';
  });

  document.addEventListener('click', (e) => {
    if (!offerSearchInput.contains(e.target) && !offerDropdown.contains(e.target)) {
      offerDropdown.classList.add('hidden');
    }
  });

  // Form submission
  form.addEventListener('submit', async (e) => {
    e.preventDefault();

    // Reset errors
    document.getElementById('nameError').classList.remove('visible');
    document.getElementById('geoError').classList.remove('visible');
    document.getElementById('offerError').classList.remove('visible');

    let hasErrors = false;
    const name = nameInput.value.trim();
    if (!name) {
      const err = document.getElementById('nameError');
      err.textContent = 'Укажите название кампании';
      err.classList.add('visible');
      hasErrors = true;
    }

    const geo = geoInput.value.trim();
    if (!geo) {
      const err = document.getElementById('geoError');
      err.textContent = 'Укажите хотя бы один GEO-код страны';
      err.classList.add('visible');
      hasErrors = true;
    }

    const offerId = selectedOfferIdInput.value;
    if (!offerId) {
      const err = document.getElementById('offerError');
      err.textContent = 'Выберите оффер из списка';
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
      geo: geo,
      offer_id: parseInt(offerId, 10),
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
      resultVerificationStatus.textContent = data.verification_status === 'verified' ? 'Подтверждено KT' : data.verification_status;
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
    selectedOfferIdInput.value = '';
    selectedOfferBadge.classList.add('hidden');
    namePreview.textContent = 'test_novikov_...';
    previewGeoText.textContent = 'не указаны';
    previewOfferText.textContent = 'Оффер не выбран';
  });
});
