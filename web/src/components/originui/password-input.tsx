// https://originui.com/r/comp-23.json

'use client';

import React, { useId } from 'react';
import { useTranslation as useUiTranslation } from 'react-i18next';
import { Input, InputProps } from '../ui/input';

export default React.forwardRef<HTMLInputElement, InputProps>(
  function PasswordInput({ ...props }, ref) {
    const { t: tUi } = useUiTranslation();
    const id = useId();

    return (
      <div className="*:not-first:mt-2">
        {/* <Label htmlFor={id}>Show/hide password input</Label> */}
        <div className="relative">
          <Input
            id={id}
            className="pe-9"
            placeholder={tUi('login.passwordLabel')}
            type="password"
            ref={ref}
            {...props}
          />
        </div>
      </div>
    );
  },
);
