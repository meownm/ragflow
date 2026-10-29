import '@/components/workbench/workbench.css';
import { Outlet, useLocation } from 'react-router';
import { Header } from './components/header';

export function RootLayoutContainer({ children }: React.PropsWithChildren) {
  const { pathname } = useLocation();
  const workbench =
    /^\/(business-documents|technical-specifications|sql-projects|sql-assistant)(\/|$)/.test(
      pathname,
    );
  return (
    <div
      className={`size-full min-w-0 grid grid-flow-col grid-cols-1 grid-rows-[auto_1fr] ${workbench ? 'rf-workbench' : ''}`}
    >
      <Header className={workbench ? 'rf-shell-header' : 'px-5 py-4'} />

      <main className="size-full min-w-0 overflow-hidden">{children}</main>
    </div>
  );
}

export default function RootLayout() {
  return (
    <RootLayoutContainer>
      <Outlet />
    </RootLayoutContainer>
  );
}
