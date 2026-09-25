$api = Get-Content -Path "backend\api.py" -Raw
$api = $api -replace '"neurospectrum_model.pt"', 'os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "neurospectrum_model.pt")'
Set-Content -Path "backend\api.py" -Value $api

$inf = Get-Content -Path "backend\inference_service.py" -Raw
$inf = $inf -replace '"neurospectrum_model.pt"', 'os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "neurospectrum_model.pt")'
Set-Content -Path "backend\inference_service.py" -Value $inf

$trn = Get-Content -Path "backend\training_service.py" -Raw
$trn = $trn -replace '"neurospectrum_model.pt"', 'os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "neurospectrum_model.pt")'
Set-Content -Path "backend\training_service.py" -Value $trn
