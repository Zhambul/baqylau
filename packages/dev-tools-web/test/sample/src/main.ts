import { mount } from 'svelte';

import App from './App.svelte';
import { total } from './total';

const target = document.body;
mount(App, { target });
target.dataset.total = String(total([1, 2]));
