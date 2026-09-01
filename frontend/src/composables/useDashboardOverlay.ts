import { readonly, ref } from 'vue'

const communicationPanelOpen = ref(false)
const aiControlPanelOpen = ref(false)
const sidePanelsCollapsed = ref(false)

export function useDashboardOverlay() {
  function openCommunicationPanel() {
    aiControlPanelOpen.value = false
    communicationPanelOpen.value = true
  }

  function closeCommunicationPanel() {
    communicationPanelOpen.value = false
  }

  function toggleCommunicationPanel() {
    const nextOpen = !communicationPanelOpen.value
    communicationPanelOpen.value = nextOpen
    if (nextOpen) aiControlPanelOpen.value = false
  }

  function openAiControlPanel() {
    communicationPanelOpen.value = false
    aiControlPanelOpen.value = true
  }

  function closeAiControlPanel() {
    aiControlPanelOpen.value = false
  }

  function toggleAiControlPanel() {
    const nextOpen = !aiControlPanelOpen.value
    aiControlPanelOpen.value = nextOpen
    if (nextOpen) communicationPanelOpen.value = false
  }

  function toggleSidePanels() {
    sidePanelsCollapsed.value = !sidePanelsCollapsed.value
  }

  return {
    communicationPanelOpen: readonly(communicationPanelOpen),
    aiControlPanelOpen: readonly(aiControlPanelOpen),
    sidePanelsCollapsed: readonly(sidePanelsCollapsed),
    openCommunicationPanel,
    closeCommunicationPanel,
    toggleCommunicationPanel,
    openAiControlPanel,
    closeAiControlPanel,
    toggleAiControlPanel,
    toggleSidePanels,
  }
}
