<script lang="ts">
  import { BasicForm, createForm } from '@sjsf/form';
  import { createFormIdBuilder } from '@sjsf/form/id-builders/modern';
  import { createFormMerger } from '@sjsf/form/mergers/modern';
  import { resolver } from '@sjsf/form/resolvers/basic';
  import { translation } from '@sjsf/form/translations/en';
  import { createFormValidator } from '@sjsf/cfworker-validator';
  import { theme } from '@sjsf/basic-theme';
  import '@sjsf/basic-theme/css/basic.css';
  import { untrack } from 'svelte';

  let {
    schema,
    value,
    disabled,
    onsave,
  }: {
    schema: Record<string, unknown>;
    value: unknown;
    disabled: boolean;
    onsave: (value: unknown) => void;
  } = $props();

  // The parent remounts this form for each accepted settings revision, so
  // the first schema and value are the ones to edit.
  const form = createForm<unknown>({
    theme,
    schema: untrack(() => schema),
    initialValue: untrack(() => value),
    resolver,
    translation,
    merger: createFormMerger,
    validator: createFormValidator,
    idBuilder: createFormIdBuilder,
    get disabled() {
      return disabled;
    },
    onSubmit: (submitted: unknown) => {
      onsave(submitted);
    },
  });
</script>

<div class="settings-form">
  <BasicForm {form} />
</div>

<style>
  .settings-form :global(button[type='submit']) {
    margin-top: 8px;
  }
</style>
