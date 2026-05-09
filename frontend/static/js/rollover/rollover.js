/**
 * Rollover module
 * Orchestrator that composes functionality from focused sub-modules.
 */
import { initializeRollover, clearRolloverSuggestions, populateRolloverSuggestionsTable, initializeRolloverTooltips, selectOptionToRoll } from './rollover-ui.js';

document.addEventListener('DOMContentLoaded', initializeRollover);

export {
    initializeRollover,
    clearRolloverSuggestions,
    populateRolloverSuggestionsTable,
    initializeRolloverTooltips,
    selectOptionToRoll,
};
