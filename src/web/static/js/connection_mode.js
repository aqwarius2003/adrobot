// Connection mode switching and in-flight/dirty guards (v3.0)
document.addEventListener('DOMContentLoaded', () => {
  const modeBtnReal = document.getElementById('modeBtnReal');
  const modeBtnDemo = document.getElementById('modeBtnDemo');
  const modal = document.getElementById('modeSwitchModal');
  const modalPublishBtn = document.getElementById('modalPublishAndSwitchBtn');
  const modalCancelBtn = document.getElementById('modalCancelAndSwitchBtn');
  const modalStayBtn = document.getElementById('modalStayBtn');
  const affectedList = document.getElementById('modeSwitchAffectedStreams');

  const currentMode = (window.APP_CONFIG && window.APP_CONFIG.current_mode) || 'real';

  // Display notice if we just switched mode
  const switchedNotice = sessionStorage.getItem('mode_switched_notice');
  if (switchedNotice) {
    if (switchedNotice === 'demo') {
      showToast(
        'Демо включено. Используются тестовые кампании и пять виртуальных офферов. Изменения сохраняются только в симуляторе.',
        'info'
      );
    } else if (switchedNotice === 'real') {
      showToast(
        'Демо выключено. Вы подключены к Keitaro. Изменения после Push to KT отправляются в настоящий трекер.',
        'info'
      );
    }
    sessionStorage.removeItem('mode_switched_notice');
  }

  function getDirtyStreams() {
    if (window.EDITOR_STATE && window.EDITOR_STATE.currentCampaignData) {
      const streams = window.EDITOR_STATE.currentCampaignData.streams || [];
      return streams.filter((s) => s.sync_status === 'dirty');
    }
    return [];
  }

  function getInFlightStreams() {
    if (window.EDITOR_STATE && window.EDITOR_STATE.currentCampaignData) {
      const streams = window.EDITOR_STATE.currentCampaignData.streams || [];
      return streams.filter((s) => ['publishing', 'verifying', 'unknown'].includes(s.sync_status));
    }
    return [];
  }

  async function switchMode(targetMode) {
    if (targetMode === currentMode) return;

    // Check simulator unsaved dirty state if present
    if (window.SIMULATOR_STATE && window.SIMULATOR_STATE.isDirty) {
      const ok = confirm('Есть несохранённые изменения формы симулятора. Переключить режим и потерять их?');
      if (!ok) return;
    }

    // Check in-flight operations
    const inFlight = getInFlightStreams();
    if (inFlight.length > 0) {
      const names = inFlight.map((s) => s.name).join(', ');
      showToast(
        `Переключение заблокировано: потоки [${names}] находятся в процессе синхронизации (${inFlight[0].sync_status}). Дождитесь завершения.`,
        'error'
      );
      return;
    }

    // Check dirty streams
    const dirty = getDirtyStreams();
    if (dirty.length > 0) {
      showDirtyModal(targetMode, dirty);
      return;
    }

    await performSwitch(targetMode);
  }

  async function performSwitch(targetMode) {
    try {
      const safeFetch =
        (window.apiClient && window.apiClient.fetch) ||
        window.apiFetch ||
        fetch;
      const res = await safeFetch('/api/auth/switch-mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: targetMode }),
      });

      if (res.status === 401 && targetMode === 'real') {
        // Need authentication to enter real mode
        window.location.href = '/login?return_to=/real/editor';
        return;
      }

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Не удалось обновить режим на сервере');
      }

      sessionStorage.setItem('mode_switched_notice', targetMode);

      // Restore separate campaign selection per mode if available
      const savedCampaign = localStorage.getItem(`adrobot_selected_campaign_${targetMode}`);
      let targetUrl = `/${targetMode}/editor`;
      if (savedCampaign) {
        targetUrl += `?campaign_id=${encodeURIComponent(savedCampaign)}`;
      }

      window.location.href = targetUrl;
    } catch (err) {
      console.error('Mode switch failed:', err);
      showToast(err.message, 'error');
    }
  }

  let lastFocusedElement = null;
  let modalKeyHandler = null;

  function closeModal() {
    if (!modal) return;
    modal.classList.add('hidden');
    const layout = document.querySelector('.app-layout');
    if (layout) {
      layout.removeAttribute('aria-hidden');
    }
    if (modalKeyHandler) {
      document.removeEventListener('keydown', modalKeyHandler);
      modalKeyHandler = null;
    }
    if (lastFocusedElement && typeof lastFocusedElement.focus === 'function') {
      try {
        lastFocusedElement.focus();
      } catch (e) {}
    }
  }

  function showDirtyModal(targetMode, dirtyStreams) {
    if (!modal) return;

    lastFocusedElement = document.activeElement;

    affectedList.innerHTML = dirtyStreams
      .map(
        (s) => `
        <div class="affected-stream-item">
          <strong>${escapeHtml(s.name)}</strong>
          <span class="badge badge-warning">Неопубликован</span>
        </div>`
      )
      .join('');

    modal.classList.remove('hidden');
    const layout = document.querySelector('.app-layout');
    if (layout) {
      layout.setAttribute('aria-hidden', 'true');
    }

    // Modal focus trap & Escape key handler
    modalKeyHandler = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeModal();
        return;
      }
      if (e.key === 'Tab') {
        const focusable = Array.from(
          modal.querySelectorAll('button:not([disabled]), [tabindex]:not([tabindex="-1"])')
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener('keydown', modalKeyHandler);

    // Initial focus on Stay button for safety
    if (modalStayBtn) {
      modalStayBtn.focus();
    }

    // Handle Publish & Switch
    modalPublishBtn.onclick = async () => {
      modalPublishBtn.disabled = true;
      modalPublishBtn.textContent = 'Публикация...';
      try {
        if (window.EDITOR_STATE && window.EDITOR_STATE.pushAllDirty) {
          await window.EDITOR_STATE.pushAllDirty();
        }
        closeModal();
        await performSwitch(targetMode);
      } catch (err) {
        showToast(`Ошибка публикации: ${err.message}`, 'error');
        modalPublishBtn.disabled = false;
        modalPublishBtn.textContent = 'Опубликовать и переключить';
      }
    };

    // Handle Cancel & Switch
    modalCancelBtn.onclick = async () => {
      modalCancelBtn.disabled = true;
      try {
        if (window.EDITOR_STATE && window.EDITOR_STATE.cancelAllDirty) {
          await window.EDITOR_STATE.cancelAllDirty();
        }
        closeModal();
        await performSwitch(targetMode);
      } catch (err) {
        showToast(`Ошибка сброса черновика: ${err.message}`, 'error');
        modalCancelBtn.disabled = false;
      }
    };

    // Handle Stay
    modalStayBtn.onclick = () => {
      closeModal();
    };
  }

  function escapeHtml(str) {
    if (!str) return '';
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  if (modeBtnReal) {
    modeBtnReal.addEventListener('click', () => switchMode('real'));
  }
  if (modeBtnDemo) {
    modeBtnDemo.addEventListener('click', () => switchMode('demo'));
  }
});
