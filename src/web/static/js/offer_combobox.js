/**
 * Accessible Offer Autocomplete Combobox Component (Task 3.1 §3 & §5)
 *
 * Implements WAI-ARIA Combobox pattern:
 * - role="combobox", aria-autocomplete="list", aria-expanded, aria-controls, aria-activedescendant
 * - role="listbox", role="option", aria-selected
 * - Full keyboard navigation (ArrowDown, ArrowUp, Enter, Escape, Tab)
 * - Mouse selection with safe blur prevention
 * - Stale response rejection
 * - Collision & viewport-aware dropup positioning
 */

class OfferCombobox {
  /**
   * @param {Object} options
   * @param {HTMLInputElement} options.input - Text input element
   * @param {HTMLElement} options.dropdown - Dropdown container element
   * @param {HTMLButtonElement} options.addBtn - Add button to enable/disable
   * @param {Function} options.searchFn - Async function(query) returning array of offers
   * @param {Function} options.getExcludedOfferIds - Function returning Set or Array of IDs to filter out
   * @param {Function} options.onSelect - Callback(offer) when an offer is selected
   * @param {Function} [options.onClear] - Callback() when selection is cleared
   * @param {HTMLElement} [options.cardElement] - Parent stream-card for z-index elevating
   */
  constructor(options) {
    this.input = options.input;
    this.dropdown = options.dropdown;
    this.addBtn = options.addBtn;
    this.searchFn = options.searchFn;
    this.getExcludedOfferIds = options.getExcludedOfferIds || (() => new Set());
    this.onSelect = options.onSelect || (() => {});
    this.onClear = options.onClear || (() => {});
    this.cardElement = options.cardElement || this.input.closest('.stream-card');

    this.selectedOffer = null;
    this.currentResults = [];
    this.highlightedIndex = -1;
    this.searchSeq = 0;
    this.debounceTimer = null;
    this.isOpen = false;

    this._uniqueId = 'combobox-' + Math.random().toString(36).substring(2, 9);
    this._initAria();
    this._bindEvents();
  }

  _initAria() {
    this.input.setAttribute('role', 'combobox');
    this.input.setAttribute('aria-autocomplete', 'list');
    this.input.setAttribute('aria-expanded', 'false');
    this.input.setAttribute('aria-controls', this.dropdown.id || (this.dropdown.id = this._uniqueId + '-listbox'));
    this.input.setAttribute('aria-haspopup', 'listbox');
    this.dropdown.setAttribute('role', 'listbox');
    this.dropdown.setAttribute('tabindex', '-1');
  }

  _bindEvents() {
    this._onInputHandler = () => this._onInput();
    this._onKeyDownHandler = (e) => this._onKeyDown(e);
    this._onFocusHandler = () => {
      const q = this.input.value.trim();
      if (q.length >= 1 && !this.selectedOffer) {
        this._executeSearch(q);
      }
    };

    this.input.addEventListener('input', this._onInputHandler);
    this.input.addEventListener('keydown', this._onKeyDownHandler);
    this.input.addEventListener('focus', this._onFocusHandler);

    // Close on outside click
    this._onPointerDown = (e) => {
      if (!this.input.contains(e.target) && !this.dropdown.contains(e.target)) {
        this.close();
      }
    };
    document.addEventListener('pointerdown', this._onPointerDown);

    // Keep dropdown aligned on scroll or resize while open
    this._onResize = () => {
      if (this.isOpen) this._updatePosition();
    };
    this._onScroll = () => {
      if (this.isOpen) this._updatePosition();
    };
    window.addEventListener('resize', this._onResize);
    window.visualViewport?.addEventListener('resize', this._onResize);
    window.addEventListener('scroll', this._onScroll, true);
  }

  destroy() {
    clearTimeout(this.debounceTimer);
    if (this.input) {
      if (this._onInputHandler) this.input.removeEventListener('input', this._onInputHandler);
      if (this._onKeyDownHandler) this.input.removeEventListener('keydown', this._onKeyDownHandler);
      if (this._onFocusHandler) this.input.removeEventListener('focus', this._onFocusHandler);
    }
    if (this._onPointerDown) {
      document.removeEventListener('pointerdown', this._onPointerDown);
    }
    if (this._onResize) {
      window.removeEventListener('resize', this._onResize);
      window.visualViewport?.removeEventListener('resize', this._onResize);
    }
    if (this._onScroll) {
      window.removeEventListener('scroll', this._onScroll, true);
    }
    this.close();
  }

  _onInput() {
    clearTimeout(this.debounceTimer);

    // If text was modified after an offer was selected, clear selection
    if (this.selectedOffer) {
      this.selectedOffer = null;
      if (this.addBtn) this.addBtn.disabled = true;
      this.onClear();
    }

    const query = this.input.value.trim();
    if (query.length < 1) {
      this.close();
      return;
    }

    this.debounceTimer = setTimeout(() => {
      this._executeSearch(query);
    }, 180);
  }

  async _executeSearch(query) {
    const seq = ++this.searchSeq;
    this._showLoading();

    try {
      const offers = await this.searchFn(query);
      if (seq !== this.searchSeq) return; // Discard stale response

      const excluded = new Set(this.getExcludedOfferIds());
      const available = (offers || []).filter((o) => !excluded.has(o.id));

      this.currentResults = available;
      this._renderResults();
    } catch (err) {
      if (seq !== this.searchSeq) return;
      console.warn('Combobox search failed:', err);
      this._showError('Ошибка поиска офферов');
    }
  }

  _showLoading() {
    this.dropdown.innerHTML = '<div class="dropdown-status dropdown-loading">Поиск офферов...</div>';
    this.open();
  }

  _showError(msg) {
    this.dropdown.innerHTML = `<div class="dropdown-status dropdown-error">${this._escape(msg)}</div>`;
    this.open();
  }

  _renderResults() {
    if (this.currentResults.length === 0) {
      this.dropdown.innerHTML = '<div class="dropdown-status dropdown-empty">Ничего не найдено</div>';
      this.highlightedIndex = -1;
      this.input.removeAttribute('aria-activedescendant');
      this.open();
      return;
    }

    this.dropdown.innerHTML = this.currentResults
      .map((o, idx) => {
        const optId = `${this._uniqueId}-opt-${idx}`;
        const isHighlighted = idx === 0;
        return `
          <div
            id="${optId}"
            class="dropdown-item ${isHighlighted ? 'highlighted' : ''}"
            role="option"
            data-index="${idx}"
            aria-selected="${isHighlighted ? 'true' : 'false'}"
          >
            <span class="dropdown-item-id">[ID: ${this._escape(String(o.id))}]</span>
            <span class="dropdown-item-name">${this._escape(o.name || `Offer #${o.id}`)}</span>
          </div>`;
      })
      .join('');

    // Pre-select first item for predictable keyboard navigation
    this.highlightedIndex = 0;
    this.input.setAttribute('aria-activedescendant', `${this._uniqueId}-opt-0`);

    // Mouse selection handlers
    this.dropdown.querySelectorAll('.dropdown-item[data-index]').forEach((el) => {
      // Use pointerdown to select before input loses focus
      el.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        const idx = parseInt(el.getAttribute('data-index'), 10);
        this._selectIndex(idx);
      });

      el.addEventListener('pointerenter', () => {
        const idx = parseInt(el.getAttribute('data-index'), 10);
        this._setHighlight(idx, false);
      });
    });

    this.open();
  }

  _onKeyDown(e) {
    if (e.key === 'Escape') {
      if (this.isOpen) {
        e.preventDefault();
        this.close();
      }
      return;
    }

    if (e.key === 'Tab') {
      this.close();
      return;
    }

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (!this.isOpen) {
        const q = this.input.value.trim();
        if (q.length >= 1) this._executeSearch(q);
      } else {
        this._moveHighlight(1);
      }
      return;
    }

    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (this.isOpen) {
        this._moveHighlight(-1);
      }
      return;
    }

    if (e.key === 'Enter') {
      if (this.isOpen && this.highlightedIndex >= 0 && this.highlightedIndex < this.currentResults.length) {
        e.preventDefault();
        e.stopPropagation();
        this._selectIndex(this.highlightedIndex);
      }
      return;
    }
  }

  _moveHighlight(delta) {
    if (this.currentResults.length === 0) return;
    let nextIndex = this.highlightedIndex + delta;
    if (nextIndex < 0) nextIndex = this.currentResults.length - 1;
    if (nextIndex >= this.currentResults.length) nextIndex = 0;
    this._setHighlight(nextIndex, true);
  }

  _setHighlight(index, scrollIntoView) {
    this.highlightedIndex = index;
    const items = this.dropdown.querySelectorAll('.dropdown-item');
    items.forEach((item, idx) => {
      const isMatch = idx === index;
      item.classList.toggle('highlighted', isMatch);
      item.setAttribute('aria-selected', isMatch ? 'true' : 'false');
      if (isMatch) {
        this.input.setAttribute('aria-activedescendant', item.id);
        if (scrollIntoView) {
          item.scrollIntoView({ block: 'nearest' });
        }
      }
    });
  }

  _selectIndex(index) {
    const offer = this.currentResults[index];
    if (!offer) return;

    this.selectedOffer = offer;
    this.input.value = `[ID: ${offer.id}] ${offer.name || `Offer #${offer.id}`}`;
    if (this.addBtn) this.addBtn.disabled = false;

    this.close();
    this.onSelect(offer);
  }

  _updatePosition() {
    const rect = this.input.getBoundingClientRect();
    const viewport = window.visualViewport;
    const top = viewport ? viewport.offsetTop : 0;
    const bottom = top + (viewport ? viewport.height : window.innerHeight);
    const spaceBelow = bottom - rect.bottom - 12;
    const spaceAbove = rect.top - top - 12;

    // If space below is less than 220px and above has more space, open dropup
    const dropup = spaceBelow < 220 && spaceAbove > spaceBelow;
    if (dropup) {
      this.dropdown.classList.add('dropup');
    } else {
      this.dropdown.classList.remove('dropup');
    }
    this.dropdown.style.maxHeight = `${Math.max(0, Math.min(220, dropup ? spaceAbove : spaceBelow))}px`;
  }

  open() {
    this._updatePosition();
    this.dropdown.classList.remove('hidden');
    this.input.setAttribute('aria-expanded', 'true');
    this.isOpen = true;
    if (this.cardElement) {
      this.cardElement.classList.add('has-open-dropdown');
    }
  }

  close() {
    this.dropdown.classList.add('hidden');
    this.input.setAttribute('aria-expanded', 'false');
    this.input.removeAttribute('aria-activedescendant');
    this.isOpen = false;
    if (this.cardElement) {
      this.cardElement.classList.remove('has-open-dropdown');
    }
  }

  reset() {
    this.input.value = '';
    this.selectedOffer = null;
    this.currentResults = [];
    this.highlightedIndex = -1;
    if (this.addBtn) this.addBtn.disabled = true;
    this.close();
  }

  _escape(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
}

window.OfferCombobox = OfferCombobox;
