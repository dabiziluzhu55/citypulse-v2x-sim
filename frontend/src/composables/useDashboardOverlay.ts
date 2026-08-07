import { readonly, ref } from 'vue'

const communicationPanelOpen = ref(false)
const sidePanelsCollapsed = ref(false)

export function useDashboardOverlay() {
  function openCommunicationPanel() {
    communicationPanelOpen.value = true
  }

  function closeCommunicationPanel() {
    communicationPanelOpen.value = false
  }

  function toggleCommunicationPanel() {
    communicationPanelOpen.value = !communicationPanelOpen.value
  }

  function toggleSidePanels() {
    sidePanelsCollapsed.value = !sidePanelsCollapsed.value
  }

  return {
    communicationPanelOpen: readonly(communicationPanelOpen),
    sidePanelsCollapsed: readonly(sidePanelsCollapsed),
    openCommunicationPanel,
    closeCommunicationPanel,
    toggleCommunicationPanel,
    toggleSidePanels,
  }
}
