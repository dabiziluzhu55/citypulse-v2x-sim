import { onMounted, ref } from 'vue'

import { fetchScenarioTemplates } from '../api/scenario'

import { enrichScenarioTemplate } from '../constants/mapDefaults'

import { FALLBACK_SCENARIO_TEMPLATES } from '../constants/scenarioTemplatesFallback'

import type { ScenarioTemplate } from '../types/scenario'



function mergeTemplates(apiTemplates: ScenarioTemplate[]): ScenarioTemplate[] {

  const map = new Map<string, ScenarioTemplate>()



  for (const template of FALLBACK_SCENARIO_TEMPLATES.map(enrichScenarioTemplate)) {

    map.set(template.template_id, template)

  }



  for (const template of apiTemplates.map(enrichScenarioTemplate)) {

    map.set(template.template_id, { ...map.get(template.template_id), ...template })

  }



  return Array.from(map.values())

}



export function useScenarioTemplates() {

  const templates = ref<ScenarioTemplate[]>(FALLBACK_SCENARIO_TEMPLATES.map(enrichScenarioTemplate))

  const loading = ref(true)

  const error = ref<string | null>(null)

  const fromApi = ref(false)



  async function load() {

    loading.value = true

    error.value = null



    try {

      const response = await fetchScenarioTemplates()

      templates.value = mergeTemplates(response.templates)

      fromApi.value = true

    } catch (err) {

      templates.value = FALLBACK_SCENARIO_TEMPLATES.map(enrichScenarioTemplate)

      fromApi.value = false

      error.value = null

    } finally {

      loading.value = false

    }

  }



  onMounted(() => {

    void load()

  })



  return {

    templates,

    loading,

    error,

    fromApi,

    refresh: load,

  }

}

