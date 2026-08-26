import { SelectWithSearch } from '@/components/originui/select-with-search';
import { Button } from '@/components/ui/button';
import message from '@/components/ui/message';
import {
  discoverEvaWikiProjects,
  EvaWikiProject,
} from '@/services/data-source-service';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ControllerRenderProps,
  useFormContext,
  useWatch,
} from 'react-hook-form';
import { useSearchParams } from 'react-router';

type EvaWikiProjectFieldProps = {
  field: ControllerRenderProps;
};

export default function EvaWikiProjectField({
  field,
}: EvaWikiProjectFieldProps) {
  const form = useFormContext();
  const [searchParams] = useSearchParams();
  const connectorId = searchParams.get('id') || undefined;
  const apiBaseUrl = useWatch({
    control: form.control,
    name: 'config.api_base_url',
  });
  const token = useWatch({
    control: form.control,
    name: 'config.credentials.eva_api_token',
  });
  const verifySsl = useWatch({
    control: form.control,
    name: 'config.verify_ssl',
  });
  const [projects, setProjects] = useState<EvaWikiProject[]>([]);
  const [loading, setLoading] = useState(false);
  const loadedIdentityRef = useRef('');
  const autoLoadedConnectorRef = useRef('');
  const projectId = String(field.value || '');
  const onProjectChange = field.onChange;

  const discoveryIdentity = useMemo(
    () =>
      `${String(connectorId || '')}|${String(apiBaseUrl || '')}|${String(token || '')}|${Boolean(verifySsl)}`,
    [apiBaseUrl, connectorId, token, verifySsl],
  );

  const options = useMemo(() => {
    const loadedOptions = projects.map((project) => ({
      value: project.id,
      label: project.code ? `${project.name} (${project.code})` : project.name,
    }));
    if (
      projectId &&
      !loadedOptions.some((option) => option.value === projectId)
    ) {
      loadedOptions.unshift({
        value: projectId,
        label: projectId,
      });
    }
    return loadedOptions;
  }, [projectId, projects]);

  const loadProjects = useCallback(async () => {
    const config = form.getValues('config') || {};
    if (
      !connectorId &&
      (!config.api_base_url || !config.credentials?.eva_api_token)
    ) {
      message.error('Enter the EVA API URL and token first.');
      return;
    }

    setLoading(true);
    try {
      const { data: response } = await discoverEvaWikiProjects({
        connector_id: connectorId,
        config,
      });
      if (response.code !== 0 || !Array.isArray(response.data)) {
        message.error(response.message || 'Unable to load EVA projects.');
        return;
      }

      const nextProjects = response.data as EvaWikiProject[];
      setProjects(nextProjects);
      loadedIdentityRef.current = discoveryIdentity;
      if (nextProjects.length === 0) {
        if (!connectorId) {
          onProjectChange('');
        }
        message.error('No accessible EVA projects were found.');
        return;
      }

      const currentProjectIsAccessible = nextProjects.some(
        (project) => project.id === projectId,
      );
      if (connectorId) {
        if (projectId && !currentProjectIsAccessible) {
          message.error('The configured EVA project is no longer accessible.');
        }
      } else if (nextProjects.length === 1) {
        onProjectChange(nextProjects[0].id);
      } else if (projectId && !currentProjectIsAccessible) {
        onProjectChange('');
      }
    } catch {
      message.error('Unable to load EVA projects.');
    } finally {
      setLoading(false);
    }
  }, [connectorId, discoveryIdentity, form, onProjectChange, projectId]);

  useEffect(() => {
    if (
      loadedIdentityRef.current &&
      loadedIdentityRef.current !== discoveryIdentity
    ) {
      loadedIdentityRef.current = '';
      setProjects([]);
      if (!connectorId) {
        onProjectChange('');
      }
    }
  }, [connectorId, discoveryIdentity, onProjectChange]);

  useEffect(() => {
    if (
      connectorId &&
      apiBaseUrl &&
      autoLoadedConnectorRef.current !== connectorId
    ) {
      autoLoadedConnectorRef.current = connectorId;
      void loadProjects();
    }
  }, [apiBaseUrl, connectorId, loadProjects]);

  return (
    <div className="flex w-full gap-2">
      <div className="min-w-0 flex-1">
        <SelectWithSearch
          value={projectId}
          onChange={onProjectChange}
          options={options}
          placeholder={
            projects.length > 0
              ? 'Select an EVA project'
              : 'Load accessible projects'
          }
          emptyData="No accessible EVA projects"
          disabled={Boolean(connectorId) || loading || options.length === 0}
          testId="eva-wiki-project-select"
          optionTestIdPrefix="eva-wiki-project-option-"
        />
      </div>
      <Button
        type="button"
        variant="outline"
        onClick={loadProjects}
        disabled={loading || (!connectorId && (!apiBaseUrl || !token))}
        loading={loading}
      >
        {projects.length > 0 ? 'Reload projects' : 'Load projects'}
      </Button>
    </div>
  );
}
