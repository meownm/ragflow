import '@testing-library/jest-dom';
import React from 'react';
import { TextDecoder, TextEncoder } from 'util';
import 'whatwg-fetch';

Object.assign(globalThis, { React, TextDecoder, TextEncoder });
Object.assign(globalThis, { jestImportMetaGlob: () => ({}) });
