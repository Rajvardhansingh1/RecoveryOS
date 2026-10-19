import { HomeWorkspace } from "@/components/HomeWorkspace";
import { RunList } from "@/components/RunList";

export default function Home() {
  return (
    <>
      <HomeWorkspace />
      <section className="section" aria-labelledby="runs-h">
        <h2 id="runs-h">Runs</h2>
        <RunList />
      </section>
    </>
  );
}
