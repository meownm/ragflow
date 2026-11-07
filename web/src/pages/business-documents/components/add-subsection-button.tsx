import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useId, useState, type MouseEvent } from 'react';

export type SubmitSubsection = (
  title: string,
  text: string,
  done: (error?: string) => void,
) => void;

export function AddSubsectionButton({
  parent,
  disabled,
  onSubmit,
}: {
  parent: string;
  disabled: boolean;
  onSubmit: SubmitSubsection;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [error, setError] = useState('');

  function close() {
    setOpen(false);
    setTitle('');
    setText('');
    setError('');
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) setOpen(true);
        else if (!disabled) close();
      }}
    >
      <DialogTrigger asChild>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="shrink-0"
          disabled={disabled}
          onMouseUp={(event: MouseEvent<HTMLButtonElement>) =>
            event.stopPropagation()
          }
        >
          Добавить подраздел
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Добавить подраздел</DialogTitle>
          <DialogDescription>В раздел «{parent}»</DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (disabled || !title.trim()) return;
            setError('');
            onSubmit(title.trim(), text.trim(), (failure) => {
              if (failure) setError(failure);
              else close();
            });
          }}
        >
          <div className="space-y-2">
            <label htmlFor={`${id}-title`}>Название подраздела</label>
            <Input
              id={`${id}-title`}
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              maxLength={500}
              required
              disabled={disabled}
            />
          </div>
          <div className="space-y-2">
            <label htmlFor={`${id}-text`}>
              Текст подраздела (необязательно)
            </label>
            <Textarea
              id={`${id}-text`}
              value={text}
              onChange={(event) => setText(event.target.value)}
              maxLength={20000}
              rows={4}
              disabled={disabled}
            />
          </div>
          {error && (
            <p role="alert" className="text-sm text-state-error">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={close}
              disabled={disabled}
            >
              Отмена
            </Button>
            <Button type="submit" disabled={disabled || !title.trim()}>
              Добавить
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
