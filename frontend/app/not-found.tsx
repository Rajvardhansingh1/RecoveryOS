export default function NotFound() {
  return (
    <>
      <h1>Page not found</h1>
      <p className="empty">
        There is nothing at this address. <a href="/">Go to all runs</a> or{" "}
        <a href="/benchmark">see the benchmark</a>.
      </p>
    </>
  );
}
