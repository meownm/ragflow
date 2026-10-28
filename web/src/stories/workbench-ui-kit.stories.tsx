import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { WorkbenchNotice } from '@/components/workbench/notice';
import { StatePanel } from '@/components/workbench/state-panel';
import '@/components/workbench/workbench.css';
import '@/inter.less';
import type { Meta, StoryObj } from '@storybook/react-webpack5';
import { useEffect } from 'react';
import { MemoryRouter } from 'react-router';

function WorkbenchKit({ theme = 'light' }: { theme?: 'light' | 'dark' }) {
  useEffect(() => {
    const previous = document.documentElement.className;
    document.documentElement.classList.toggle('dark', theme === 'dark');
    return () => {
      document.documentElement.className = previous;
    };
  }, [theme]);
  return (
    <MemoryRouter>
      <div className={theme}>
        <main className="rf-workbench min-h-screen p-6 space-y-8">
          <header>
            <h1 className="text-2xl font-semibold">UI-kit рабочих разделов</h1>
            <p className="text-text-secondary mt-2">
              БТ · ТЗ · SQL · Администрирование
            </p>
          </header>
          <section className="space-y-4">
            <h2 className="text-lg font-medium">
              Кнопки · варианты, размеры и состояния
            </h2>
            {(
              [
                'default',
                'secondary',
                'accent',
                'outline',
                'ghost',
                'destructive',
              ] as const
            ).map((variant) => (
              <div key={variant} className="flex flex-wrap items-center gap-3">
                <span className="w-24 text-text-secondary">{variant}</span>
                {(['xs', 'sm', 'default', 'lg', 'xl'] as const).map((size) => (
                  <Button key={size} variant={variant} size={size}>
                    Сохранить
                  </Button>
                ))}
                <Button variant={variant} disabled>
                  Недоступно
                </Button>
                <Button variant={variant} loading>
                  Сохраняется
                </Button>
              </div>
            ))}
          </section>
          <section className="grid grid-cols-2 gap-6 max-w-4xl">
            <div className="space-y-3">
              <h2 className="text-lg font-medium">Поля и управление</h2>
              <label className="block space-y-2">
                <span>Название документа</span>
                <Input className="h-10" placeholder="Введите название" />
              </label>
              <label className="block space-y-2">
                <span>Замечание</span>
                <Textarea
                  className="min-h-28"
                  placeholder="Опишите изменение…"
                />
              </label>
              <Select defaultValue="all">
                <SelectTrigger aria-label="Статус">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Все статусы</SelectItem>
                  <SelectItem value="active">Активные</SelectItem>
                </SelectContent>
              </Select>
              <label className="flex gap-2 items-center">
                <Checkbox defaultChecked />
                Показывать завершённые
              </label>
              <label className="flex gap-2 items-center">
                <Switch defaultChecked />
                Автоматическое обновление
              </label>
            </div>
            <div className="space-y-3">
              <h2 className="text-lg font-medium">Статусы и сообщения</h2>
              <WorkbenchNotice
                intent="warning"
                title="Есть неприменённые изменения"
              >
                Откройте предложение для сравнения с сохранённым текстом.
              </WorkbenchNotice>
              <div className="flex flex-wrap gap-2">
                <Badge variant="success">Сохранено</Badge>
                <Badge variant="warning">Требует внимания</Badge>
                <Badge variant="destructive">Не сохранено</Badge>
                <Badge variant="secondary">Только чтение</Badge>
              </div>
              <StatePanel
                title="Не удалось загрузить документ"
                onRetry={() => {}}
              >
                Сохранённая работа доступна после восстановления соединения.
              </StatePanel>
              <Dialog>
                <DialogTrigger asChild>
                  <Button variant="outline">Проверить диалог</Button>
                </DialogTrigger>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>Завершить запрос?</DialogTitle>
                    <DialogDescription>
                      Строки результата будут удалены. Документ и история
                      сохранятся.
                    </DialogDescription>
                  </DialogHeader>
                  <DialogFooter>
                    <Button variant="destructive">Завершить запрос</Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            </div>
          </section>
          <section className="space-y-4 max-w-4xl">
            <h2 className="text-lg font-medium">
              Таблица · наведение и постоянный выбор
            </h2>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Документ</TableHead>
                  <TableHead>Состояние</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow>
                  <TableCell>Обычная строка · наведите указатель</TableCell>
                  <TableCell>В работе</TableCell>
                </TableRow>
                <TableRow data-state="selected" aria-selected>
                  <TableCell>
                    Выбранная строка · маркер остаётся при наведении
                  </TableCell>
                  <TableCell>В работе</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </section>
          <Tabs defaultValue="document">
            <TabsList>
              <TabsTrigger value="document">Документ</TabsTrigger>
              <TabsTrigger value="history">История</TabsTrigger>
              <TabsTrigger value="disabled" disabled>
                Недоступно
              </TabsTrigger>
            </TabsList>
            <TabsContent value="document">
              <p>
                Сохранённый текст остаётся отдельно от предложенных изменений.
              </p>
            </TabsContent>
            <TabsContent value="history">
              <p>Выберите сохранённую редакцию.</p>
            </TabsContent>
          </Tabs>
        </main>
      </div>
    </MemoryRouter>
  );
}

const meta = {
  title: 'RAGFlow/Workbench UI-kit',
  component: WorkbenchKit,
  parameters: { layout: 'fullscreen' },
  tags: ['autodocs'],
} satisfies Meta<typeof WorkbenchKit>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Light: Story = { args: { theme: 'light' } };
export const Dark: Story = { args: { theme: 'dark' } };
