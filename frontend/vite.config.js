import {defineConfig} from 'vite';
export default defineConfig({server:{proxy:{'/api':'http://127.0.0.1:8017','/metrics':'http://127.0.0.1:8017'}}});
