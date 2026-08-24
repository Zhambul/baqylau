import 'vite/modulepreload-polyfill';
import '../../static/style.css';

import { mount } from 'svelte';

import App from './app/App.svelte';

const target = document.getElementById('app');
if (!(target instanceof HTMLElement)) {
  throw new Error('the dashboard mount element is missing');
}

mount(App, { target });
