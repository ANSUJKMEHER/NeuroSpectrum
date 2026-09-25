$content = Get-Content -Path "src\energy.py" -Raw
$idx = $content.IndexOf("class LocalizedNeuralPairwiseEnergy")
if ($idx -ge 0) {
    $newContent = $content.Substring(0, $idx)
    Set-Content -Path "src\energy.py" -Value $newContent
}
