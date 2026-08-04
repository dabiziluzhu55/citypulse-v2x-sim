import { readonly, ref } from 'vue'

const communicationPanelOpen = ref(false)

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

  return {
    communicationPanelOpen: readonly(communicationPanelOpen),
    openCommunicationPanel,
    closeCommunicationPanel,
    toggleCommunicationPanel,
  }
}
